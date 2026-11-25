"""1단계 - 녹화 이벤트를 사람이 읽는 업무 단계로 해석한다.

실행: python -m analyzer.interpret recordings/<id> [--no-ai] [--prompt-version v1]
결과: recordings/<id>/steps.json

역할 분담:
- 코드가 정하는 것: 동작 종류(이동/입력/선택/클릭/다운로드/제출), 입력값, 요소 위치 정보
- AI가 정하는 것: 화면에 보이는 대상 이름, 단계 설명, 의미 없는 클릭 제외, 데이터 변경 여부
"""
import argparse
from pathlib import Path
from urllib.parse import urlparse

from .common import (fill_prompt, load_prompt, now_iso, read_json, resolve_recording,
                     write_json)

BATCH_SIZE = 8   # 한 번의 AI 요청에 묶는 이벤트 수 (무료 등급 요청 수 절약)
CHANGE_WORDS = ("저장", "등록", "수정", "삭제", "승인", "신청", "확정", "제출", "출석", "작성", "전송")
LOCATOR_KEYS = ("tag", "id", "name", "input_type", "label", "group_label", "text", "selector")


def _path(url: str) -> str:
    """URL을 경로+쿼리로 줄인다 (AI에 보내는 용도)."""
    u = urlparse(url)
    return u.path + (f"?{u.query}" if u.query else "")


def prefilter(events: list[dict]) -> list[dict]:
    """AI에 보내기 전에 코드로 확실히 정리할 수 있는 것을 정리하고 동작 종류를 붙인다.

    - 첫 페이지 이동만 '이동' 단계로 남긴다 (나머지 이동은 클릭의 결과)
    - 버튼 클릭으로 일어난 submit은 제외
    - 다운로드는 바로 앞 클릭에 붙여 '다운로드' 단계로 만든다
    - 같은 입력칸을 다른 동작 없이 다시 입력했으면 앞의 입력은 제외
    """
    out: list[dict] = []
    first_nav = True
    for ev in events:
        kind = ev["type"]
        c = dict(ev)
        if kind == "navigate":
            if not first_nav:
                continue
            first_nav = False
            c["action"], c["value"] = "이동", ev["url"]
        elif kind == "submit":
            if ev.get("via") == "click":
                continue
            c["action"] = "제출"
        elif kind == "download":
            click = next((x for x in reversed(out) if x["action"] == "클릭"), None)
            if click is not None:
                click["action"] = "다운로드"
                click["download_filename"] = ev.get("filename", "")
            continue
        elif kind == "input":
            c["action"] = "입력"
        elif kind == "change":
            c["action"] = "선택"
            c["option_value"] = ev.get("value", "")
            c["value"] = ev.get("option_text") or ev.get("value", "")
        elif kind == "click":
            if ev.get("input_type") == "radio":
                c["action"] = "선택"
            else:
                c["action"] = "클릭"
                c.pop("value", None)
        else:
            continue
        # 같은 칸 반복 입력: 직전 단계가 같은 칸 입력이면 앞의 것을 버린다
        if c["action"] in ("입력", "선택") and out and out[-1]["action"] == c["action"] \
                and out[-1].get("selector") == c.get("selector"):
            out.pop()
        out.append(c)
    return out


def _ai_view(c: dict) -> dict:
    """AI에 보낼 이벤트 요약 (불필요한 필드 제거)."""
    keys = ("seq", "action", "tag", "id", "name", "input_type", "label", "group_label",
            "short_text", "text", "value", "download_filename")
    v = {k: c[k] for k in keys if c.get(k) not in (None, "")}
    v["page"] = _path(c.get("url", ""))
    v["page_title"] = c.get("title", "")
    return v


def default_target(c: dict) -> str:
    """규칙 기반 대상 이름."""
    if c["action"] == "이동":
        return c.get("title") or "시작 화면"
    if c.get("input_type") == "radio":
        return c.get("group_label") or c.get("name") or "선택 항목"
    name = (c.get("label") or c.get("short_text") or c.get("text") or c.get("id")
            or c.get("name") or c.get("tag", ""))
    if c.get("tag") == "a" and not c.get("short_text") and len(name) > 20:
        name = name[:20].rstrip() + "…"   # 긴 링크 문장은 줄여서 표시
    if c["action"] in ("클릭", "다운로드") and c.get("tag") in ("button", "input"):
        return f"{name} 버튼"
    return name


def default_description(c: dict, target: str) -> str:
    """규칙 기반 단계 설명."""
    a = c["action"]
    if a == "이동":
        return f"{target} 화면을 연다"
    if a == "입력":
        return f"{target}을(를) 입력한다"
    if a == "선택":
        return f"{target}을(를) 선택한다"
    if a == "다운로드":
        return f"{target}을(를) 눌러 파일({c.get('download_filename', '')})을 내려받는다"
    if a == "제출":
        return "입력한 내용을 제출한다"
    return f"{target}을(를) 클릭한다"


def _guess_changes(c: dict) -> bool:
    """버튼 문구로 데이터 변경 단계인지 추정한다 (AI 판단과 합쳐서 사용).

    메뉴 링크(a)는 '점검 결과 등록'처럼 글자가 겹쳐도 화면 이동일 뿐이므로 제외한다.
    """
    if c["action"] == "제출":
        return c.get("form_method") == "post" and "login" not in c.get("url", "")
    if c["action"] != "클릭" or c.get("tag") not in ("button", "input"):
        return False
    # POST 폼의 제출 버튼은 서버 데이터를 바꾸는 동작으로 본다 (로그인 제외)
    if c.get("form_method") == "post" and "login" not in c.get("url", ""):
        return True
    words = f"{c.get('text', '')} {c.get('label', '')} {c.get('id', '')}"
    return any(w in words for w in CHANGE_WORDS)


def _ask_ai(name: str, batch: list[dict], previous: list[str], rec_dir: Path,
            prompt_version: str) -> dict:
    """이벤트 묶음 하나를 AI에 보내 해석 결과를 받는다."""
    from .llm_client import generate_json

    prompt = fill_prompt(load_prompt("interpret", prompt_version),
                         RECORDING_NAME=name,
                         PREVIOUS="\n".join(previous[-5:]) or "(없음)",
                         EVENTS=[_ai_view(c) for c in batch])
    images = [(f"[이벤트 {c['seq']} 스크린샷]", rec_dir / c["screenshot"])
              for c in batch if c.get("screenshot") and (rec_dir / c["screenshot"]).exists()]
    return generate_json(prompt, images)


def interpret(rec: str | Path, use_ai: bool = True, prompt_version: str = "v1",
              log=print) -> Path:
    """녹화를 해석해 steps.json을 만들고 경로를 돌려준다."""
    rec_dir = resolve_recording(rec)
    data = read_json(rec_dir / "events.json")
    if data.get("kind") == "excel":   # 엑셀 녹화는 전용 해석기로
        from .excel_interpret import interpret_excel
        return interpret_excel(rec_dir, data, use_ai, prompt_version, log)
    cands = prefilter(data["events"])
    log(f"이벤트 {len(data['events'])}개 → 해석 대상 {len(cands)}개")

    ai_steps: dict[int, dict] = {}
    dropped: list[dict] = []
    if use_ai:
        previous: list[str] = []
        for i in range(0, len(cands), BATCH_SIZE):
            batch = cands[i:i + BATCH_SIZE]
            log(f"AI 해석 요청 {i // BATCH_SIZE + 1}/{(len(cands) - 1) // BATCH_SIZE + 1} "
                f"(이벤트 {batch[0]['seq']}~{batch[-1]['seq']})")
            res = _ask_ai(data.get("name", ""), batch, previous, rec_dir, prompt_version)
            seqs = {c["seq"] for c in batch}
            for s in res.get("steps", []):
                if isinstance(s, dict) and s.get("event_seq") in seqs:
                    ai_steps[s["event_seq"]] = s
                    previous.append(f"- {s.get('description', '')}")
            for d in res.get("dropped", []):
                if isinstance(d, dict) and d.get("event_seq") in seqs:
                    dropped.append(d)

    dropped_seqs = {d["event_seq"] for d in dropped}
    steps: list[dict] = []
    for c in cands:
        # AI는 '클릭'만 제외할 수 있다. 입력·선택·다운로드 등은 재실행에 꼭 필요하므로 남긴다.
        if c["seq"] in dropped_seqs and c["action"] == "클릭":
            continue
        ai = ai_steps.get(c["seq"], {})
        target = str(ai.get("target") or default_target(c))
        step = {
            "step": len(steps) + 1,
            "event_seq": c["seq"],
            "action": c["action"],
            "target": target,
            "value": c.get("value", "") if c["action"] not in ("클릭", "다운로드", "제출") else "",
            "description": str(ai.get("description") or default_description(c, target)),
            "changes_data": bool(ai.get("changes_data")) or _guess_changes(c),
            "locator": {k: c.get(k, "") for k in LOCATOR_KEYS},
            "page_url": c.get("url", ""),
            "screenshot": c.get("screenshot", ""),
        }
        if c.get("option_value"):
            step["option_value"] = c["option_value"]
        if c.get("download_filename"):
            step["download_filename"] = c["download_filename"]
        if use_ai and c["seq"] not in ai_steps and c["seq"] not in dropped_seqs:
            step["note"] = "AI 응답에 없어 규칙 기반으로 채움"
        steps.append(step)

    from .llm_client import model_name
    out = {
        "recording_id": data["id"],
        "recording_name": data.get("name", ""),
        "start_url": data.get("start_url", ""),
        "generated_at": now_iso(),
        "model": model_name() if use_ai else "규칙 기반(AI 미사용)",
        "prompt_version": prompt_version if use_ai else None,
        "steps": steps,
        "dropped": [d for d in dropped if d["event_seq"] in {c["seq"] for c in cands if c["action"] == "클릭"}],
    }
    path = write_json(rec_dir / "steps.json", out)
    log(f"단계 {len(steps)}개 → {path}")
    return path


def main() -> None:
    """명령줄 실행."""
    parser = argparse.ArgumentParser(description="녹화 → 업무 단계 해석")
    parser.add_argument("recording", help="녹화 폴더 (recordings/<id>)")
    parser.add_argument("--no-ai", action="store_true", help="AI 없이 규칙 기반으로만 해석 (테스트용)")
    parser.add_argument("--prompt-version", default="v1")
    args = parser.parse_args()
    interpret(args.recording, use_ai=not args.no_ai, prompt_version=args.prompt_version)


if __name__ == "__main__":
    main()
