"""업무·화면 개요: 이 업무가 무엇이고, 각 화면이 어떤 화면인지 정리한다 (AI 기능 1).

실행: python -m analyzer.overview recordings/<id> [--no-ai]
결과: recordings/<id>/overview.json
  {"task": {name, purpose, inputs, outputs, when}, "screens": [{key, name, description, steps, screenshot, url|sheet}],
   "model", "generated_at"}

- 화면 묶기는 코드가 한다: 웹은 페이지 주소(경로)별, 엑셀은 시트별로 이어진 단계를 한 화면으로 본다.
- AI는 화면 이름·설명과 업무 요약만 쓴다 (스크린샷을 함께 보냄). AI를 끄면 화면 제목과 동작 목록만으로 정리한다.
- 단계 해석(analyzer.interpret) 뒤에 자동으로 실행된다.
"""
import argparse
from pathlib import Path
from urllib.parse import urlparse

from .common import fill_prompt, load_prompt, now_iso, read_json, resolve_recording, write_json
from .josa import fix_josa

MAX_AI_SCREENS = 8   # 한 번에 AI에 보낼 화면 수 (스크린샷 수 = 요청 크기)


def group_screens(data: dict, events: dict) -> list[dict]:
    """이어진 단계를 화면 단위로 묶는다."""
    titles = {e["seq"]: e.get("title", "") for e in events.get("events", [])}
    screens: list[dict] = []
    for s in data["steps"]:
        ex = s.get("excel")
        if ex is not None:
            if ex.get("op") in ("open", "save"):
                key = "파일" if ex.get("op") == "open" else "저장"
                title = s["target"] if ex.get("op") == "open" else "결과 파일"
            else:
                key = f"시트:{ex.get('sheet', '')}"
                title = f"'{ex.get('sheet', '')}' 시트"
        else:
            key = urlparse(s.get("page_url", "")).path or "/"
            title = titles.get(s.get("event_seq")) or s["target"]
        if screens and screens[-1]["key"] == key:
            screens[-1]["steps"].append(s["step"])
            continue
        if any(sc["key"] == key for sc in screens):   # 같은 화면에 다시 돌아온 경우는 구분
            key = f"{key}#{len(screens) + 1}"
        screens.append({"key": key, "title": title, "steps": [s["step"]], "screenshot": s.get("screenshot", ""),
                        "url": s.get("page_url", ""), "sheet": (ex or {}).get("sheet", "")})
    return screens


def _rule_based(data: dict, screens: list[dict]) -> dict:
    """AI 없이: 화면 제목과 그 화면에서 한 동작으로만 설명."""
    by_no = {s["step"]: s for s in data["steps"]}
    out_screens = []
    for sc in screens:
        acts = [f"{by_no[n]['action']}({by_no[n]['target']})" for n in sc["steps"]][:5]
        more = " 등" if len(sc["steps"]) > 5 else ""
        out_screens.append({"key": sc["key"], "name": sc["title"],
                            "description": fix_josa(f"이 화면에서 {len(sc['steps'])}개 동작을 한다: {', '.join(acts)}{more}")})
    inputs = [s["target"] for s in data["steps"] if s["action"] in ("입력", "선택") or s.get("variable_ok")]
    outputs = [f"다운로드 파일 {s['download_filename']}" for s in data["steps"] if s.get("download_filename")]
    outputs += [s["value"] for s in data["steps"] if s.get("excel", {}).get("op") == "save"]
    outputs += [f"{s['target']} (데이터 변경)" for s in data["steps"] if s.get("changes_data")]
    return {"task": {"name": data.get("recording_name", ""), "purpose": "", "inputs": inputs[:8], "outputs": outputs[:6], "when": ""},
            "screens": out_screens}


def _ask_ai(data: dict, screens: list[dict], rec_dir: Path, prompt_version: str) -> dict:
    from .llm_client import generate_json
    by_no = {s["step"]: s for s in data["steps"]}
    view, images = [], []
    for sc in screens[:MAX_AI_SCREENS]:
        view.append({"key": sc["key"], "title": sc["title"], "address": urlparse(sc["url"]).path if sc["url"] else sc["sheet"],
                     "steps": [{"step": n, "action": by_no[n]["action"], "target": by_no[n]["target"],
                                "value": by_no[n].get("value", ""), "description": by_no[n]["description"]} for n in sc["steps"]]})
        if sc["screenshot"] and (rec_dir / sc["screenshot"]).exists():
            images.append((f"[화면 key={sc['key']} 스크린샷]", rec_dir / sc["screenshot"]))
    prompt = fill_prompt(load_prompt("overview", prompt_version), RECORDING_NAME=data.get("recording_name", ""),
                         KIND="엑셀 파일 작업" if data.get("kind") == "excel" else "웹 사이트 작업", SCREENS=view)
    res = generate_json(prompt, images)
    return res if isinstance(res, dict) else {}


def validate(raw: dict, base: dict) -> dict:
    """AI 결과 검증: 없는 화면 key는 버리고, 빠진 화면·항목은 규칙 기반 값으로 채운다."""
    task = raw.get("task") if isinstance(raw.get("task"), dict) else {}
    out_task = {}
    for k, default in base["task"].items():
        v = task.get(k, default)
        if isinstance(default, list):
            v = [fix_josa(str(x)) for x in v] if isinstance(v, list) else default
        else:
            v = fix_josa(str(v or default))
        out_task[k] = v
    ai = {s.get("key"): s for s in raw.get("screens", []) if isinstance(s, dict)}
    screens = []
    for sc in base["screens"]:
        a = ai.get(sc["key"], {})
        screens.append({"key": sc["key"], "name": fix_josa(str(a.get("name") or sc["name"])),
                        "description": fix_josa(str(a.get("description") or sc["description"])),
                        "by_ai": bool(a.get("description"))})
    return {"task": out_task, "screens": screens}


def make_overview(rec: str | Path, use_ai: bool = True, prompt_version: str = "v1", log=print) -> Path:
    """overview.json을 만든다."""
    rec_dir = resolve_recording(rec)
    data = read_json(rec_dir / "steps.json")
    events = read_json(rec_dir / "events.json")
    screens = group_screens(data, events)
    base = _rule_based(data, screens)
    model = "규칙 기반(AI 미사용)"
    result = base
    if use_ai:
        try:
            log("AI 업무·화면 개요 요청")
            result = validate(_ask_ai(data, screens, rec_dir, prompt_version), base)
            from .llm_client import model_name
            model = model_name()
        except Exception as e:
            log(f"AI 개요 실패 → 규칙 기반으로 정리 ({str(e).splitlines()[0][:150]})")
    for sc, meta in zip(result["screens"], screens):
        sc.update(steps=meta["steps"], screenshot=meta["screenshot"], url=meta["url"], sheet=meta["sheet"])
    out = {"recording_id": data["recording_id"], "generated_at": now_iso(), "model": model, **result}
    path = write_json(rec_dir / "overview.json", out)
    log(f"업무 개요: {out['task'].get('purpose') or out['task'].get('name')}")
    for sc in out["screens"]:
        log(f"  화면 [{sc['name']}] {sc['description']} (단계 {sc['steps'][0]}~{sc['steps'][-1]})")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="업무·화면 개요 만들기")
    parser.add_argument("recording")
    parser.add_argument("--no-ai", action="store_true")
    parser.add_argument("--prompt-version", default="v1")
    args = parser.parse_args()
    make_overview(args.recording, use_ai=not args.no_ai, prompt_version=args.prompt_version)


if __name__ == "__main__":
    main()
