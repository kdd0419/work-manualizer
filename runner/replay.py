"""v1 기준선: 녹화 기록을 그대로 재생한다.

실행: python -m runner.replay recordings/<id> [--headless]

- 요소는 녹화된 CSS 선택자로만 찾고, 입력값도 녹화된 값을 그대로 쓴다.
- 실패하면 그 단계에서 멈추고 스크린샷을 남긴다.
- 결과: runs/<id>_<타임스탬프>/result.json
"""
import argparse
import json
from pathlib import Path
from typing import Callable

from playwright.sync_api import sync_playwright

from .common import (STOP_REASONS, VIEWPORT, fail_screenshot, find_element, finish_result,
                     new_result, new_run_dir, page_messages, save_result, settle)


def load_events(rec_dir: Path) -> dict:
    """events.json을 읽는다."""
    return json.loads((rec_dir / "events.json").read_text(encoding="utf-8"))


def _target(ev: dict) -> str:
    """로그에 보여줄 대상 이름."""
    return ev.get("label") or ev.get("text") or ev.get("id") or ev.get("filename") or ev.get("url", "")


def replay(rec_dir: str | Path, headless: bool = False,
           log: Callable[[str], None] = print) -> dict:
    """녹화 폴더를 재생하고 결과 dict를 돌려준다."""
    rec_dir = Path(rec_dir)
    data = load_events(rec_dir)
    events = data["events"]
    run_dir = new_run_dir(data["id"])
    result = new_result("replay", str(rec_dir), run_dir)
    result["total"] = len(events)
    consumed: set[int] = set()   # 앞 단계에서 함께 처리한 이벤트(다운로드)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(viewport=VIEWPORT, accept_downloads=True, locale="ko-KR")
        page = context.new_page()
        first_nav_done = False

        for i, ev in enumerate(events):
            n, kind = ev["seq"], ev["type"]
            step = {"step": n, "action": kind, "target": _target(ev), "status": "성공", "message": ""}
            try:
                if n in consumed:
                    step["message"] = "앞 단계 클릭에서 함께 처리됨"
                elif kind == "navigate":
                    if not first_nav_done:
                        page.goto(ev["url"])
                        first_nav_done = True
                    else:
                        page.wait_for_url(ev["url"], timeout=5000)   # 녹화 때와 같은 주소로 이동했는지 확인
                elif kind == "submit" and ev.get("via") == "click":
                    step["status"] = "건너뜀"
                    step["message"] = "버튼 클릭으로 이미 제출됨"
                elif kind == "download":
                    raise RuntimeError("download_missing")
                else:
                    el, _ = find_element(page, ev, strategies=("selector",))
                    if el is None:
                        raise RuntimeError("element_not_found")
                    if kind == "input":
                        el.fill(ev.get("value", ""))
                    elif kind == "change":
                        el.select_option(ev.get("value", ""))
                    elif kind == "submit":
                        el.evaluate("f => f.requestSubmit()")
                    elif kind == "click":
                        nxt = next((e for e in events[i + 1:] if e["type"] != "navigate"), None)
                        if nxt and nxt["type"] == "download":
                            with page.expect_download(timeout=10000) as info:
                                el.click()
                            dl = info.value
                            dl.save_as(str(run_dir / dl.suggested_filename))
                            result["downloads"].append(dl.suggested_filename)
                            consumed.add(nxt["seq"])
                            step["message"] = f"다운로드: {dl.suggested_filename}"
                        else:
                            el.click()
                    settle(page)
                    result["messages"] += [m for m in page_messages(page) if m not in result["messages"]]
            except Exception as e:
                code = str(e) if str(e) in STOP_REASONS else "action_failed"
                step["status"] = "실패"
                step["message"] = STOP_REASONS[code] + ("" if code != "action_failed" else f" ({e})")
                step["screenshot"] = fail_screenshot(page, run_dir, n)
                result["stopped_at"] = n
                result["stop_reason"] = code
                result["stop_message"] = step["message"]
            result["steps"].append(step)
            log(f"[{n:03d}] {step['status']:<3} {kind:<9} {step['target']} {step['message']}")
            if step["status"] == "실패":
                break
        browser.close()

    finish_result(result)
    save_result(run_dir, result)
    log(f"총 {result['total']}단계 중 {result['succeeded']}단계 성공 → {run_dir / 'result.json'}")
    return result


def main() -> None:
    """명령줄 실행."""
    parser = argparse.ArgumentParser(description="녹화 그대로 재생 (v1 기준선)")
    parser.add_argument("recording", help="녹화 폴더 (recordings/<id>)")
    parser.add_argument("--headless", action="store_true", help="브라우저 창 없이 실행")
    args = parser.parse_args()
    result = replay(args.recording, headless=args.headless)
    raise SystemExit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
