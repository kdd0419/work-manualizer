"""v3 - 다음 실행 때 바뀌는 값(변수)을 찾아 매크로 파일을 만든다.

실행: python -m analyzer.variables recordings/<id> [--no-ai]
결과: macros/<id>.json  (변수 정의 + 단계 목록, 단계의 입력값은 {{변수명}} 형태)
"""
import argparse
import re
from datetime import date
from pathlib import Path

from runner.dates import apply_rule, guess_rule, is_valid_rule

from .common import (MACROS_DIR, fill_prompt, load_prompt, now_iso, read_json,
                     resolve_recording, write_json)

INPUT_ACTIONS = ("입력", "선택")


def _clean_name(name: str, used: set[str]) -> str:
    """변수 이름에서 공백·중괄호 등을 없애고, 겹치면 번호를 붙인다."""
    base = re.sub(r"[\s{}=\"']+", "", str(name or "")) or "변수"
    name, i = base, 2
    while name in used:
        name, i = f"{base}{i}", i + 1
    used.add(name)
    return name


def _is_input(step: dict) -> bool:
    """변수가 될 수 있는 단계: 웹의 입력·선택, 엑셀의 파일·필터값·채울 값 등 (해석기가 variable_ok 표시)."""
    return step["action"] in INPUT_ACTIONS or bool(step.get("variable_ok"))


def _is_file(step: dict) -> bool:
    return step["action"] == "파일 열기"


def _is_password(step: dict) -> bool:
    return step.get("locator", {}).get("input_type") == "password" or step.get("value") == "***"


def _is_login_page(step: dict) -> bool:
    return "login" in step.get("page_url", "").lower()


def _rule_based(steps: list[dict], recorded_on: date) -> dict:
    """AI 없이: 날짜는 날짜 규칙, 비밀번호는 직접 입력, 로그인 아이디는 고정, 나머지는 '녹화값 기본'."""
    variables, fixed = [], []
    for s in steps:
        if not _is_input(s):
            continue
        if _is_file(s):
            variables.append({"name": s.get("var_name") or "입력파일", "type": "file",
                              "description": "작업할 엑셀 파일 (다른 달 파일로 바꿔 실행할 수 있음)",
                              "recorded_value": s["value"], "default_rule": "recorded", "steps": [s["step"]]})
            continue
        if _is_password(s):
            rule, desc = "직접 입력", "로그인 비밀번호"
        elif _is_login_page(s):
            fixed.append({"step": s["step"], "value": s["value"], "reason": "로그인 아이디는 실행자가 같으면 바뀌지 않음"})
            continue
        else:
            rule = guess_rule(s["value"], recorded_on) or "recorded"
            desc = f"{s['target']} 값"
        variables.append({"name": s.get("var_name") or s["target"], "description": desc, "recorded_value": s["value"],
                          "default_rule": rule, "steps": [s["step"]]})
    return {"variables": variables, "fixed": fixed}


def _ask_ai(data: dict, steps: list[dict], recorded_on: date, prompt_version: str) -> dict:
    """입력·선택 단계를 AI에 보내 변수/고정값 판단을 받는다."""
    from .llm_client import generate_json

    view = [{"step": s["step"], "action": s["action"], "target": s["target"], "value": s["value"],
             "input_type": s.get("locator", {}).get("input_type", ""), "description": s["description"],
             "page": s.get("page_url", "")} for s in steps if _is_input(s)]
    prompt = fill_prompt(load_prompt("variables", prompt_version),
                         RECORDING_NAME=data.get("recording_name", ""),
                         RECORDED_DATE=recorded_on.isoformat(), STEPS=view)
    res = generate_json(prompt)
    return res if isinstance(res, dict) else {}


def validate(raw: dict, steps: list[dict], recorded_on: date) -> dict:
    """AI 결과를 검증·보정한다. 빠진 입력 단계는 규칙 기반으로 채운다."""
    by_no = {s["step"]: s for s in steps if _is_input(s)}
    used_names: set[str] = set()
    claimed: set[int] = set()
    variables, fixed = [], []

    for v in raw.get("variables", []) or []:
        if not isinstance(v, dict):
            continue
        nos = [n for n in v.get("steps", []) if n in by_no and n not in claimed]
        if not nos:
            continue
        claimed.update(nos)
        first = by_no[nos[0]]
        rule = str(v.get("default_rule", "")).strip()
        if any(_is_file(by_no[n]) for n in nos) and len(nos) > 1:
            nos = [n for n in nos if _is_file(by_no[n])]   # 파일 변수에 다른 단계를 섞지 않음
        if any(_is_password(by_no[n]) for n in nos):
            rule = "직접 입력"
        elif _is_file(first) or not is_valid_rule(rule):
            rule = "recorded" if _is_file(first) else (guess_rule(first["value"], recorded_on) or "recorded")
        var = {
            "name": _clean_name(v.get("name") or first.get("var_name") or first["target"], used_names),
            "description": str(v.get("description", "")),
            "recorded_value": first["value"],          # 녹화값은 AI가 아니라 기록에서 가져온다
            "default_rule": rule,
            "steps": nos,
        }
        if _is_file(first):
            var["type"] = "file"
        variables.append(var)

    for f in raw.get("fixed", []) or []:
        if isinstance(f, dict) and f.get("step") in by_no and f["step"] not in claimed:
            s = by_no[f["step"]]
            if _is_password(s) or _is_file(s):
                continue   # 비밀번호·입력 파일은 고정값이 될 수 없음 → 아래에서 변수로 채움
            claimed.add(f["step"])
            fixed.append({"step": f["step"], "value": s["value"], "reason": str(f.get("reason", ""))})

    # AI가 빠뜨린 입력 단계는 규칙 기반으로 채움
    rest = [s for n, s in by_no.items() if n not in claimed]
    if rest:
        fill = _rule_based(rest, recorded_on)
        for v in fill["variables"]:
            v["name"] = _clean_name(v["name"], used_names)
            v["description"] += " (AI 응답에 없어 규칙 기반으로 추가)"
            variables.append(v)
        fixed += fill["fixed"]
    variables.sort(key=lambda v: v["steps"][0])
    fixed.sort(key=lambda f: f["step"])
    return {"variables": variables, "fixed": fixed}


def build_macro(data: dict, var_info: dict) -> dict:
    """단계 목록의 입력값을 {{변수명}}으로 바꿔 매크로를 만든다."""
    step_var = {n: v for v in var_info["variables"] for n in v["steps"]}
    macro_steps = []
    for s in data["steps"]:
        m = {k: s[k] for k in ("step", "action", "target", "value", "description", "changes_data",
                               "locator", "page_url") if k in s}
        for k in ("option_value", "download_filename", "excel"):
            if k in s:
                m[k] = s[k]
        v = step_var.get(s["step"])
        if v:
            m["value"] = "{{" + v["name"] + "}}"
        macro_steps.append(m)
    return macro_steps


def make_macro(rec: str | Path, use_ai: bool = True, prompt_version: str = "v1", log=print) -> Path:
    """변수를 인식하고 macros/<id>.json을 저장한다."""
    rec_dir = resolve_recording(rec)
    steps_path = rec_dir / "steps.json"
    if not steps_path.exists():
        raise FileNotFoundError("steps.json이 없습니다. 먼저 analyzer.interpret를 실행하세요.")
    data = read_json(steps_path)
    events = read_json(rec_dir / "events.json")
    recorded_on = date.fromisoformat(events["started_at"][:10])
    steps = data["steps"]

    if use_ai:
        log("AI 변수 인식 요청")
        raw = _ask_ai(data, steps, recorded_on, prompt_version)
    else:
        raw = _rule_based(steps, recorded_on)
    var_info = validate(raw, steps, recorded_on)

    macro = {
        "id": data["recording_id"],
        "name": data.get("recording_name", ""),
        "kind": data.get("kind", "web"),
        "source_recording": str(rec_dir),
        "start_url": data.get("start_url", ""),
        "created_at": now_iso(),
        "recorded_on": recorded_on.isoformat(),
        "model": f"단계: {data.get('model', '')} / 변수: {'AI' if use_ai else '규칙 기반'}",
        "variables": var_info["variables"],
        "fixed": var_info["fixed"],
        "steps": build_macro(data, var_info),
    }
    path = write_json(MACROS_DIR / f"{data['recording_id']}.json", macro)
    for v in macro["variables"]:
        example = apply_rule(v["default_rule"], v["recorded_value"])
        log(f"  변수 {v['name']}: 녹화값={v['recorded_value']!r}, 규칙={v['default_rule']}, "
            f"오늘 실행 시={example!r}, 단계={v['steps']}")
    for f in macro["fixed"]:
        log(f"  고정 {f['step']}단계: {f['value']!r} ({f['reason']})")
    log(f"매크로 → {path}")
    return path


def main() -> None:
    """명령줄 실행."""
    parser = argparse.ArgumentParser(description="변수 인식 + 매크로 생성")
    parser.add_argument("recording", help="녹화 폴더 (recordings/<id>)")
    parser.add_argument("--no-ai", action="store_true", help="AI 없이 규칙 기반으로 변수 인식 (테스트용)")
    parser.add_argument("--prompt-version", default="v1")
    args = parser.parse_args()
    make_macro(args.recording, use_ai=not args.no_ai, prompt_version=args.prompt_version)


if __name__ == "__main__":
    main()
