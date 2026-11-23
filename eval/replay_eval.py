"""버전별 재실행 성공률 측정.

실행: python -m eval.replay_eval --version v1 v3 v4 [--only S01] [--show]
  v1: 녹화 그대로 재생 (입력값을 바꿀 수 없음)
  v3: 변수 매크로 (실패 감지 없음)
  v4: 변수 매크로 + 실패 감지
결과: docs/results/<날짜>_<버전>.md (+ 같은 이름의 .json)

성공 판정:
  - expect.download: 마지막 단계까지 완료 + 해당 파일명이 모두 다운로드됨
  - expect.message: 마지막 단계까지 완료 + 화면 메시지에 문구가 포함됨
  - expect.stop: 그 원인(예: no_result)으로 멈춰야 성공 (결과 없음을 알아차렸는지)
"""
import argparse
import json
from datetime import date
from pathlib import Path

from analyzer.common import MACROS_DIR, RECORDINGS_DIR
from runner.macro import VAR_PATTERN, MacroError, load_macro, resolve_variables, run_macro
from runner.replay import replay

from .common import RESULTS_DIR, load_index, load_scenarios

REASON_KO = {
    "element_not_found": "요소 못 찾음", "unexpected_page": "다른 페이지", "no_result": "결과 0건",
    "error_message": "오류 메시지", "download_missing": "다운로드 없음", "user_declined": "실행 취소",
    "action_failed": "동작 오류", "variable": "변수 값 없음",
}


def map_inputs(macro: dict, inputs: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """요소 id/name 기준 입력값을 매크로 변수 값으로 바꾼다. (값, 변수로 인식되지 않은 키 목록)"""
    values, unmapped = {}, []
    for key, val in inputs.items():
        hit = False
        for s in macro["steps"]:
            loc = s.get("locator", {})
            if key in (loc.get("id"), loc.get("name")) and s["action"] in ("입력", "선택"):
                names = VAR_PATTERN.findall(s.get("value", ""))
                for n in names:
                    values[n] = val
                    hit = True
                if names:
                    break
        if not hit:
            unmapped.append(key)
    return values, unmapped


def judge(result: dict, expect: dict) -> bool:
    """기대 결과와 비교해 성공 여부를 판정한다."""
    if "stop" in expect:
        return result.get("stop_reason") == expect["stop"]
    if not result.get("success"):
        return False
    if any(f not in result.get("downloads", []) for f in expect.get("download", [])):
        return False
    if "message" in expect and not any(expect["message"] in m for m in result.get("messages", [])):
        return False
    return True


def expect_text(expect: dict) -> str:
    """보고서용 기대 결과 요약."""
    parts = []
    if "download" in expect:
        parts.append("파일 " + ", ".join(expect["download"]))
    if "message" in expect:
        parts.append(f"메시지 '{expect['message']}'")
    if "stop" in expect:
        parts.append(f"{REASON_KO.get(expect['stop'], expect['stop'])}에서 멈춤")
    return " / ".join(parts)


def run_one(version: str, entry: dict, sc_set: dict, login: dict, headless: bool) -> dict:
    """시나리오의 입력 세트 하나를 실행하고 판정 결과를 돌려준다."""
    row = {"ok": False, "stopped_at": None, "reason": None, "note": sc_set.get("note", "")}
    if version == "v1":
        result = replay(RECORDINGS_DIR / entry["recording"], headless=headless, log=lambda m: None)
    else:
        macro_path = MACROS_DIR / (entry.get("macro") or f"{entry['recording']}.json")
        if not macro_path.exists():
            row.update(reason="variable", note="매크로 파일 없음 (eval.build_macros 먼저 실행)")
            return row
        macro = load_macro(macro_path)
        inputs = {"login-id": login["id"], "login-password": login["password"], **sc_set["inputs"]}
        values, unmapped = map_inputs(macro, inputs)
        unmapped = [k for k in unmapped if k not in ("login-id", "login-password")]
        if unmapped:
            row["note"] = (row["note"] + " " if row["note"] else "") + f"변수로 인식 안 됨: {', '.join(unmapped)}"
        try:
            values = resolve_variables(macro, values)
        except MacroError as e:
            row.update(reason="variable", note=str(e))
            return row
        result = run_macro(macro_path, values, headless=headless, checks=(version == "v4"),
                           allow_changes=True, log=lambda m: None)
    row.update(ok=judge(result, sc_set["expect"]), stopped_at=result.get("stopped_at"),
               reason=result.get("stop_reason"), downloads=result.get("downloads", []),
               run_dir=result.get("run_dir"))
    if not row["ok"] and result.get("success"):
        row["note"] = (row["note"] + " " if row["note"] else "") + \
            f"완료했지만 결과가 다름 (받은 파일: {', '.join(result.get('downloads', [])) or '없음'})"
    return row


def write_report(version: str, rows: list[dict], mode_note: str) -> Path:
    """docs/results/<날짜>_<버전>.md 와 .json 저장."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{date.today().isoformat()}_{version}"
    ok = sum(r["ok"] for r in rows)
    lines = [
        f"# 재실행 평가 결과 — {version}",
        "",
        f"- 날짜: {date.today().isoformat()}",
        f"- 성공률: **{ok}/{len(rows)} ({ok / len(rows) * 100:.0f}%)**" if rows else "- 실행한 항목 없음",
        f"- 매크로 생성 방식: {mode_note}" if version != "v1" else "- 방식: 녹화 그대로 재생 (입력값 변경 불가)",
        "",
        "| 업무 | 세트 | 입력값 | 기대 결과 | 결과 | 실패 단계 | 실패 원인 | 비고 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        inputs = ", ".join(f"{k}={v}" for k, v in r["inputs"].items())
        lines.append(f"| {r['scenario']} {r['name']} | {r['set']} | {inputs} | {r['expect']} | "
                     f"{'✅ 성공' if r['ok'] else '❌ 실패'} | {r['stopped_at'] or ''} | "
                     f"{REASON_KO.get(r['reason'], r['reason'] or '')} | {r['note']} |")
    md = RESULTS_DIR / f"{stem}.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (RESULTS_DIR / f"{stem}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return md


def main() -> None:
    """명령줄 실행."""
    parser = argparse.ArgumentParser(description="버전별 재실행 성공률 평가")
    parser.add_argument("--version", nargs="+", default=["v1"], choices=["v1", "v3", "v4"])
    parser.add_argument("--only", nargs="*", help="대상 시나리오 id")
    parser.add_argument("--show", action="store_true", help="브라우저 창을 보이게")
    args = parser.parse_args()

    data = load_scenarios(args.only)
    index = load_index()
    if not index:
        print("eval/recordings_index.json이 없습니다. 먼저 python -m eval.record_scenarios 를 실행하세요.")
        raise SystemExit(1)
    modes = sorted({e.get("macro_mode", "?") for e in index.values()})

    for version in args.version:
        print(f"\n######## {version}")
        rows = []
        for sc in data["scenarios"]:
            entry = index.get(sc["id"])
            if not entry:
                print(f"{sc['id']}: 녹화 없음, 건너뜀")
                continue
            for i, sc_set in enumerate(sc["input_sets"], start=1):
                row = run_one(version, entry, sc_set, data["login"], headless=not args.show)
                row.update(scenario=sc["id"], name=sc["name"], set=i, inputs=sc_set["inputs"],
                           expect=expect_text(sc_set["expect"]))
                rows.append(row)
                print(f"{sc['id']}-{i} {'성공' if row['ok'] else '실패'}"
                      f"{'' if row['ok'] else ' (' + REASON_KO.get(row['reason'], row['reason'] or '결과 불일치') + ')'}"
                      f" {row['note']}")
        path = write_report(version, rows, ", ".join(modes))
        ok = sum(r["ok"] for r in rows)
        print(f"→ {version}: {ok}/{len(rows)} 성공. 보고서: {path}")


if __name__ == "__main__":
    main()
