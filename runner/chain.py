"""업무 연결 실행: A 매크로의 결과 파일을 B 매크로의 입력 파일로 넘겨 차례로 실행한다.

연결 정의: chains/<id>.json
  {"id", "name", "steps": [{"macro": "<매크로 id>", "link_var": "<앞 결과를 넣을 파일 변수>"}, ...]}
  - 첫 단계는 link_var가 없다. 두 번째부터는 파일 변수(엑셀 매크로의 입력파일 등)에 앞 단계 결과 파일을 넣는다.
  - 앞 단계 결과 파일 = 그 실행의 다운로드 파일(웹) 또는 _결과.xlsx(엑셀). 여러 개면 첫 번째.

실행:
  python -m runner.chain chains/<id>.json [--values 값파일.json] [--allow-changes] [--no-checks] [--headless]
  값파일: {"0": {"변수": "값"}, "1": {...}}  (단계 번호별 변수 값. 실행 후 지움 — 비밀번호가 들어 있을 수 있음)
결과: runs/chain_<id>_<시각>/chain_result.json  (마지막 줄에 RESULT_JSON=경로)
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

from .common import ROOT, RUNS_DIR
from .macro import MacroError, load_macro, resolve_variables, run_macro

CHAINS_DIR = ROOT / "chains"
MACROS_DIR = ROOT / "macros"


def file_vars(macro: dict) -> list[str]:
    """매크로에서 파일을 받는 변수 이름들 (엑셀 매크로의 입력파일 등)."""
    return [v["name"] for v in macro["variables"] if v.get("type") == "file"]


def output_of(result: dict) -> Path | None:
    """실행 결과에서 다음 업무로 넘길 파일 (다운로드 또는 엑셀 결과 파일 중 첫 번째)."""
    for name in result.get("downloads", []):
        p = Path(result["run_dir"]) / name
        if p.exists() and not p.stem.endswith("_중단"):
            return p
    return None


def rel(p: Path) -> str:
    """프로젝트 기준 상대 경로 (화면 표시·매크로 변수용)."""
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


def validate(chain: dict) -> list[str]:
    """연결 정의 확인. 문제 목록(비어 있으면 정상)."""
    problems = []
    steps = chain.get("steps", [])
    if len(steps) < 2:
        problems.append("업무를 2개 이상 연결해야 합니다.")
    for i, s in enumerate(steps):
        p = MACROS_DIR / f"{s.get('macro', '')}.json"
        if not p.exists():
            problems.append(f"{i + 1}번째 업무의 매크로를 찾을 수 없습니다: {s.get('macro')}")
            continue
        if i > 0:
            fv = file_vars(load_macro(p))
            if s.get("link_var") not in fv:
                problems.append(f"{i + 1}번째 업무에 앞 결과를 넣을 파일 변수가 없습니다 "
                                f"(가능: {', '.join(fv) or '없음 — 엑셀 매크로만 파일을 받을 수 있습니다'})")
    return problems


def run_chain(chain: dict, values: dict[str, dict], headless: bool = False, checks: bool = True,
              allow_changes: bool = False, log: Callable[[str], None] = print) -> dict:
    """연결된 업무를 차례로 실행한다. 한 단계라도 실패하면 거기서 멈춘다."""
    out_dir = RUNS_DIR / f"chain_{chain['id']}_{datetime.now():%Y%m%d_%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {"chain": chain["id"], "name": chain.get("name", ""), "run_dir": str(out_dir),
               "started_at": datetime.now().isoformat(timespec="seconds"), "steps": [], "success": False,
               "stopped_at": None, "stop_message": None, "output": None}
    problems = validate(chain)
    if problems:
        summary["stop_message"] = " / ".join(problems)
        log("연결 설정 오류: " + summary["stop_message"])
        return _save(out_dir, summary)

    prev_output: Path | None = None
    total = len(chain["steps"])
    for i, step in enumerate(chain["steps"]):
        macro_path = MACROS_DIR / f"{step['macro']}.json"
        macro = load_macro(macro_path)
        title = macro.get("name") or step["macro"]
        log(f"\n━━━ [{i + 1}/{total}] {title} ━━━")
        vals = {k: str(v) for k, v in (values.get(str(i)) or {}).items()}
        if i > 0:
            if prev_output is None:
                msg = f"{i}번째 업무에서 다음 업무로 넘길 결과 파일이 나오지 않았습니다."
                summary.update(stopped_at=i + 1, stop_message=msg)
                log(f"⛔ {msg}")
                break
            vals[step["link_var"]] = rel(prev_output)
            log(f"🔗 앞 업무 결과 → {step['link_var']} = {rel(prev_output)}")
        try:
            vals = resolve_variables(macro, vals)
        except MacroError as e:
            summary.update(stopped_at=i + 1, stop_message=f"{title}: {e}")
            log(f"⛔ {e}")
            break
        result = run_macro(macro_path, vals, headless=headless, checks=checks,
                           allow_changes=allow_changes, log=log)
        out = output_of(result) if result["success"] else None
        summary["steps"].append({"step": i + 1, "macro": step["macro"], "name": title,
                                 "kind": macro.get("kind", "web"), "success": result["success"],
                                 "run_dir": result["run_dir"], "downloads": result.get("downloads", []),
                                 "output": rel(out) if out else None,
                                 "stop_message": result.get("stop_message")})
        if not result["success"]:
            summary.update(stopped_at=i + 1, stop_message=f"{title}: {result.get('stop_message') or '실패'}")
            log(f"\n⛔ 연결 실행을 {i + 1}번째 업무에서 멈췄습니다. 뒤의 업무는 실행하지 않았습니다.")
            break
        prev_output = out
    else:
        summary["success"] = True
        summary["output"] = rel(prev_output) if prev_output else None
        log(f"\n✅ 연결된 업무 {total}개를 모두 마쳤습니다." + (f" 최종 결과: {summary['output']}" if prev_output else ""))
    return _save(out_dir, summary)


def _save(out_dir: Path, summary: dict) -> dict:
    summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
    (out_dir / "chain_result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    """명령줄 실행."""
    parser = argparse.ArgumentParser(description="업무 연결 실행 (A 결과 → B 입력)")
    parser.add_argument("chain", help="연결 정의 파일 (chains/<id>.json)")
    parser.add_argument("--values", help="단계별 변수 값 JSON 파일 (읽은 뒤 지움)")
    parser.add_argument("--allow-changes", action="store_true")
    parser.add_argument("--no-checks", action="store_true")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    chain = json.loads(Path(args.chain).read_text(encoding="utf-8"))
    values = {}
    if args.values:
        vp = Path(args.values)
        values = json.loads(vp.read_text(encoding="utf-8"))
        vp.unlink(missing_ok=True)   # 비밀번호가 있을 수 있으므로 바로 지움
    summary = run_chain(chain, values, headless=args.headless, checks=not args.no_checks,
                        allow_changes=args.allow_changes)
    print(f"RESULT_JSON={Path(summary['run_dir']) / 'chain_result.json'}")
    sys.exit(0 if summary["success"] else 1)


if __name__ == "__main__":
    main()
