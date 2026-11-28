"""2단계 - steps.json으로 마크다운 업무 매뉴얼을 만든다.

실행: python -m analyzer.manual recordings/<id> [--no-ai]
결과: manuals/<id>.md

판단 기준 질문의 답변(recordings/<id>/answers.json)이 있으면
"판단 기준"과 "예외 상황" 섹션에 반영한다.
"""
import argparse
import os
from datetime import datetime
from pathlib import Path

from .common import (MANUALS_DIR, RECORDINGS_DIR, fill_prompt, load_prompt, now_iso, read_json,
                     resolve_recording)
from .josa import fix_josa


def load_answers(rec_dir: Path) -> list[dict]:
    """answers.json에서 답변이 있는 항목만 읽는다."""
    path = rec_dir / "answers.json"
    if not path.exists():
        return []
    return [a for a in read_json(path).get("answers", []) if str(a.get("answer", "")).strip()]


def _steps_for_prompt(steps: list[dict]) -> list[dict]:
    """AI에 보낼 단계 요약."""
    keys = ("step", "action", "target", "value", "description", "changes_data", "download_filename")
    return [{k: s[k] for k in keys if k in s and s[k] not in ("", None)} for s in steps]


def _rule_based(data: dict, answers: list[dict]) -> dict:
    """AI 없이 만드는 기본 매뉴얼 내용."""
    steps = data["steps"]
    if data.get("kind") == "excel":
        stem = data.get("source_file", "원본").rsplit(".", 1)[0]
        cautions = [f"{s['step']}단계는 조건에 맞는 행을 지웁니다. 결과 파일에서 남은 행 수를 확인하세요."
                    for s in steps if s["action"] == "행 삭제"]
        if any(s["action"] in ("아래 행 추가", "셀 입력", "열 채우기") for s in steps):
            cautions.append("수식의 범위는 파일의 행 수에 맞춰 자동으로 늘어납니다. 합계·건수가 맞는지 확인하세요.")
        cautions.append("파일 양식(시트 이름, 머리글 순서)이 바뀌면 매크로가 첫 단계에서 멈춥니다.")
        return {
            "title": data.get("recording_name") or data["recording_id"],
            "purpose": f"'{data.get('recording_name', '')}' 엑셀 작업을 처리한다.",
            "purpose_is_guess": True,
            "prerequisites": ["Microsoft Excel",
                              f"작업할 엑셀 파일 (녹화 때 사용한 파일: {data.get('source_file', '')}, 같은 양식이어야 함)"],
            "outputs": [f"결과 파일: {stem}_결과.xlsx (원본 파일은 바뀌지 않음)"],
            "cautions": cautions,
            "criteria": [f"{a['question']} → {a['answer']}" for a in answers],
            "exceptions": [],
        }
    outputs = [f"다운로드 파일: {s['download_filename']}" for s in steps if s.get("download_filename")]
    outputs += [f"저장된 데이터: {s['target']} 단계에서 저장" for s in steps if s["changes_data"]]
    cautions = [f"{s['step']}단계({s['target']})는 데이터를 변경합니다. 실행 전에 입력값을 확인하세요."
                for s in steps if s["changes_data"]]
    return {
        "title": data.get("recording_name") or data["recording_id"],
        "purpose": f"'{data.get('recording_name', '')}' 업무를 처리한다.",
        "purpose_is_guess": True,
        "prerequisites": ["업무 시스템 계정"],
        "outputs": outputs,
        "cautions": cautions,
        "criteria": [f"{a['question']} → {a['answer']}" for a in answers],
        "exceptions": [],
    }


def _bullets(items: list[str], empty: str = "- (없음)") -> str:
    """목록을 마크다운 글머리표로."""
    items = [str(i).strip() for i in items or [] if str(i).strip()]
    return "\n".join(f"- {i}" for i in items) if items else empty


def render(data: dict, content: dict, rec_dir: Path, answers: list[dict], overview: dict | None = None) -> str:
    """매뉴얼 마크다운을 만든다. 처리 순서와 스크린샷은 코드가 직접 채운다."""
    rec_id = data["recording_id"]
    shot_base = os.path.relpath(rec_dir, MANUALS_DIR).replace("\\", "/")
    task = (overview or {}).get("task", {})
    purpose, is_guess = content.get("purpose", ""), content.get("purpose_is_guess")
    if is_guess and task.get("purpose"):   # 규칙 기반 '추정' 목적보다 AI 개요의 목적이 낫다
        purpose, is_guess = task["purpose"], False
    guess = " *(추정)*" if is_guess else ""
    lines = [
        f"# {content.get('title') or rec_id}",
        "",
        f"> 녹화: `{rec_id}` · 생성: {now_iso()} · 해석: {data.get('model', '')}"
        + (f" · 개요: {overview.get('model', '')}" if overview else ""),
        "",
        "## 목적",
        f"{purpose}{guess}",
        "",
    ]
    if overview:
        info = []
        if task.get("inputs"):
            info.append(f"**매번 정하는 값**: {', '.join(task['inputs'])}")
        if task.get("outputs"):
            info.append(f"**결과물**: {', '.join(task['outputs'])}")
        if task.get("when"):
            info.append(f"**하는 때**: {task['when']}")
        if info:
            lines += ["## 업무 개요", _bullets(info), ""]
        if overview.get("screens"):
            lines.append("## 화면 설명")
            for sc in overview["screens"]:
                rng = f"{sc['steps'][0]}단계" if len(sc["steps"]) == 1 else f"{sc['steps'][0]}~{sc['steps'][-1]}단계"
                lines.append(f"- **{sc['name']}** ({rng}) — {sc['description']}")
            lines.append("")
    lines += [
        "## 준비물",
        _bullets(content.get("prerequisites")),
        "",
        "## 처리 순서",
    ]
    for s in data["steps"]:
        detail = []
        if s.get("value") and (s["action"] in ("입력", "선택", "이동") or s.get("excel")):
            detail.append(f"값: `{s['value']}`")
        if s.get("download_filename"):
            detail.append(f"파일: `{s['download_filename']}`")
        if s.get("changes_data"):
            detail.append("**데이터 변경 단계**")
        lines.append(f"{s['step']}. **{s['description']}** ({s['action']} · {s['target']})"
                     + (f"  \n    {' · '.join(detail)}" if detail else ""))
        if s.get("screenshot"):
            lines.append(f"\n    ![{s['step']}단계]({shot_base}/{s['screenshot']})\n")
    lines += ["", "## 결과물", _bullets(content.get("outputs"))]
    if answers:
        lines += ["", "## 판단 기준", _bullets(content.get("criteria")),
                  "", "## 예외 상황", _bullets(content.get("exceptions"))]
    lines += ["", "## 주의사항", _bullets(content.get("cautions")), ""]
    if answers:
        lines += ["## 부록: 담당자 확인 내용", ""]
        lines += [f"- **Q.** {a['question']}  \n  **A.** {a['answer']}" for a in answers]
        lines.append("")
    return "\n".join(lines)


MAX_HISTORY = 20   # 녹화 하나당 보관할 이전 버전 수


def split_blocks(md: str) -> list[str]:
    """매뉴얼을 고칠 수 있는 조각으로 나눈다: 제목 / 각 '## 섹션' / 번호 단계 하나하나.

    번호 목록 한 줄(1. …)은 어느 제목 아래에 있든 각각 한 조각 — '처리 순서' 제목을 지우거나 바꿔도
    단계들이 한 덩어리로 합쳐지지 않는다. 조각을 그대로 이어 붙이면 원래 마크다운과 같다.
    """
    import re
    blocks: list[str] = []
    cur: list[str] = []
    for line in md.splitlines(keepends=True):
        if (line.startswith("## ") or re.match(r"^\d+\. ", line)) and cur:
            blocks.append("".join(cur))
            cur = []
        cur.append(line)
    if cur:
        blocks.append("".join(cur))
    return blocks


def backup_manual(rec_id: str, reason: str = "") -> Path | None:
    """지금 매뉴얼을 recordings/<id>/manual_history/<시각>.md 로 보관한다 (없으면 아무것도 안 함)."""
    src = MANUALS_DIR / f"{rec_id}.md"
    if not src.exists():
        return None
    d = RECORDINGS_DIR / rec_id / "manual_history"
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"{datetime.now():%Y%m%d_%H%M%S_%f}.md"
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    for old in sorted(d.glob("*.md"))[:-MAX_HISTORY]:
        old.unlink()
    return dest


def make_manual(rec: str | Path, use_ai: bool = True, prompt_version: str = "v1",
                log=print) -> Path:
    """매뉴얼을 만들어 manuals/<id>.md에 저장하고 경로를 돌려준다."""
    rec_dir = resolve_recording(rec)
    steps_path = rec_dir / "steps.json"
    if not steps_path.exists():
        raise FileNotFoundError("steps.json이 없습니다. 먼저 analyzer.interpret를 실행하세요.")
    data = read_json(steps_path)
    answers = load_answers(rec_dir)

    if use_ai:
        from .llm_client import generate_json
        log("AI 매뉴얼 작성 요청")
        prompt = fill_prompt(load_prompt("manual", prompt_version),
                             RECORDING_NAME=data.get("recording_name", ""),
                             STEPS=_steps_for_prompt(data["steps"]),
                             ANSWERS=[{"question": a["question"], "answer": a["answer"]} for a in answers])
        content = generate_json(prompt)
        if not isinstance(content, dict):
            content = {}
        base = _rule_based(data, answers)
        content = {k: content.get(k, base[k]) for k in base}
    else:
        content = _rule_based(data, answers)

    # 업무·화면 개요 (없으면 지금 만든다 — 예전에 해석한 녹화)
    ov_path = rec_dir / "overview.json"
    if not ov_path.exists():
        from .overview import make_overview
        try:
            make_overview(rec_dir, use_ai=use_ai, log=log)
        except Exception as e:
            log(f"업무 개요를 만들지 못했습니다: {str(e).splitlines()[0][:150]}")
    overview = read_json(ov_path) if ov_path.exists() else None

    MANUALS_DIR.mkdir(exist_ok=True)
    path = MANUALS_DIR / f"{data['recording_id']}.md"
    if backup_manual(data["recording_id"], "다시 생성 전"):
        log("기존 매뉴얼을 이전 버전으로 보관했습니다 (관리 화면의 '↶ 이전 버전'으로 되돌릴 수 있음)")
    path.write_text(fix_josa(render(data, content, rec_dir, answers, overview)), encoding="utf-8")
    (rec_dir / "manual_edited.json").unlink(missing_ok=True)   # 새로 만들었으니 '직접 수정됨' 표시 해제
    log(f"매뉴얼 → {path}")
    return path


def main() -> None:
    """명령줄 실행."""
    parser = argparse.ArgumentParser(description="업무 매뉴얼 생성")
    parser.add_argument("recording", help="녹화 폴더 (recordings/<id>)")
    parser.add_argument("--no-ai", action="store_true", help="AI 없이 기본 틀로 작성 (테스트용)")
    parser.add_argument("--prompt-version", default="v1")
    args = parser.parse_args()
    make_manual(args.recording, use_ai=not args.no_ai, prompt_version=args.prompt_version)


if __name__ == "__main__":
    main()
