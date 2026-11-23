"""평가 전체를 한 번에: 데이터 초기화 → 시나리오 녹화 → 매크로 생성 → v1/v3/v4 평가.

실행: python -m eval.run_all [--no-ai]
가상 업무 시스템(http://localhost:5000)이 켜져 있어야 한다.
"""
import argparse
import subprocess
import sys

from .common import ROOT

BASE_DATE = "2026-11-18"


def step(title: str, args: list[str]) -> None:
    """하위 명령 하나를 실행하고, 실패하면 중단한다."""
    print(f"\n========== {title}", flush=True)
    code = subprocess.call([sys.executable, "-m", *args], cwd=ROOT)
    if code != 0:
        print(f"'{title}' 단계가 실패했습니다 (종료 코드 {code}). 평가를 중단합니다.", flush=True)
        raise SystemExit(code)


def main() -> None:
    """명령줄 실행."""
    parser = argparse.ArgumentParser(description="평가 전체 실행")
    parser.add_argument("--no-ai", action="store_true", help="매크로를 규칙 기반으로 생성")
    args = parser.parse_args()

    step("1/4 가상 데이터 초기화", ["mock_system.seed", "--base-date", BASE_DATE])
    step("2/4 시나리오 10개 자동 녹화", ["eval.record_scenarios"])
    step("3/4 해석 + 매크로 생성", ["eval.build_macros"] + (["--no-ai"] if args.no_ai else []))
    step("4/4 v1 / v3 / v4 재실행 평가", ["eval.replay_eval", "--version", "v1", "v3", "v4"])
    # 평가는 고정 기준일(2026-11-18) 데이터로 하고, 끝나면 시연용으로 오늘 기준 데이터로 되돌린다
    step("정리: 가상 데이터를 오늘 기준으로 되돌림", ["mock_system.seed"])
    print("\n평가 완료. 결과: docs/results/", flush=True)


if __name__ == "__main__":
    main()
