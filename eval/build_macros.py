"""녹화한 평가 시나리오마다 해석 → 변수 인식 → 매크로 생성을 한 번에 실행한다.

실행: python -m eval.build_macros [--no-ai] [--only S01 S02]
- AI 모드는 시나리오마다 Gemini 요청이 2~3번 일어난다. 무료 한도에 걸리면 --only로 나눠 실행하세요.
"""
import argparse

from analyzer.interpret import interpret
from analyzer.llm_client import LLMError
from analyzer.variables import make_macro

from .common import load_index, load_scenarios, save_index


def main() -> None:
    """명령줄 실행."""
    parser = argparse.ArgumentParser(description="평가용 매크로 일괄 생성")
    parser.add_argument("--no-ai", action="store_true", help="AI 없이 규칙 기반으로 생성")
    parser.add_argument("--only", nargs="*", help="대상 시나리오 id")
    args = parser.parse_args()

    index = load_index()
    if not index:
        print("eval/recordings_index.json이 없습니다. 먼저 python -m eval.record_scenarios 를 실행하세요.")
        raise SystemExit(1)
    use_ai = not args.no_ai
    for sc in load_scenarios(args.only)["scenarios"]:
        entry = index.get(sc["id"])
        if not entry:
            print(f"{sc['id']}: 녹화 없음, 건너뜀")
            continue
        print(f"\n=== {sc['id']} {sc['name']} ({'AI' if use_ai else '규칙 기반'})")
        try:
            interpret(entry["recording"], use_ai=use_ai)
            path = make_macro(entry["recording"], use_ai=use_ai)
        except LLMError as e:
            print(f"AI 오류로 중단: {e}")
            break
        entry["macro"] = path.name
        entry["macro_mode"] = "AI" if use_ai else "규칙 기반"
        save_index(index)
    print("\n완료. 다음: python -m eval.replay_eval --version v1 v3 v4")


if __name__ == "__main__":
    main()
