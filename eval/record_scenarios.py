"""평가 시나리오를 사람 대신 자동으로 시연·녹화한다.

실행: python -m eval.record_scenarios [--only S01 S02] [--show]
결과: recordings/eval_<id>_..., eval/recordings_index.json

녹화기(recorder)는 사람이 할 때와 똑같이 이벤트를 수집한다. 자동 조작은 시연을 대신할 뿐이다.
"""
import argparse

from playwright.sync_api import Page

from recorder.record import record

from .common import load_index, load_scenarios, save_index


def make_script(scenario: dict, login: dict):
    """시나리오의 actions를 실제 브라우저 조작 함수로 만든다 (로그인 포함)."""
    def run(page: Page) -> None:
        page.fill("#login-id", login["id"])
        page.fill("#login-password", login["password"])
        page.click("#login-button")
        page.wait_for_load_state()
        for act in scenario["actions"]:
            if "click" in act:
                page.click(act["click"])
            elif "fill" in act:
                page.fill(act["fill"], act["value"])
            elif "select" in act:
                if "label" in act:
                    page.select_option(act["select"], label=act["label"])
                else:
                    page.select_option(act["select"], act["value"])
            elif "check" in act:
                page.check(act["check"])
            elif "download" in act:
                with page.expect_download():
                    page.click(act["download"])
            page.wait_for_load_state()
            page.wait_for_timeout(150)   # 사람처럼 약간 쉬어 이벤트가 섞이지 않게
    return run


def main() -> None:
    """명령줄 실행."""
    parser = argparse.ArgumentParser(description="평가 시나리오 자동 녹화")
    parser.add_argument("--only", nargs="*", help="녹화할 시나리오 id (예: S01 S07)")
    parser.add_argument("--show", action="store_true", help="브라우저 창을 보이게")
    args = parser.parse_args()

    data = load_scenarios(args.only)
    index = load_index()
    for sc in data["scenarios"]:
        print(f"\n=== {sc['id']} {sc['name']} 녹화")
        out = record(f"{data['base_url']}/login", f"eval_{sc['id']}_{sc['name']}",
                     headless=not args.show, script=make_script(sc, data["login"]),
                     log=lambda m: None)
        index[sc["id"]] = {"recording": out.name, "macro": None}
        print(f"→ {out}")
    save_index(index)
    print(f"\n녹화 {len(data['scenarios'])}개 완료. 다음: python -m eval.build_macros")


if __name__ == "__main__":
    main()
