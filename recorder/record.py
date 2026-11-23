"""브라우저 업무 녹화기.

실행:
  python -m recorder.record --url http://localhost:5000 --name 위탁업체점검조회
  → Chromium 창에서 업무를 시연하고, 터미널에서 Enter를 누르거나 브라우저를 닫으면 종료

저장:
  recordings/<이름_타임스탬프>/events.json
  recordings/<...>/screenshots/001.jpg ...
  recordings/<...>/downloads/<다운로드 파일>
"""
import argparse
import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
RECORDINGS_DIR = ROOT / "recordings"
INJECT_JS = Path(__file__).with_name("inject.js")
VIEWPORT = {"width": 1280, "height": 800}
JPEG_QUALITY = 70


def safe_name(name: str) -> str:
    """폴더 이름에 쓸 수 없는 문자를 _로 바꾼다."""
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_") or "녹화"


class Recorder:
    """이벤트를 순번·스크린샷과 함께 모아 events.json으로 저장한다."""

    def __init__(self, out_dir: Path, log: Callable[[str], None] = print):
        self.out_dir = out_dir
        self.shot_dir = out_dir / "screenshots"
        self.dl_dir = out_dir / "downloads"
        self.shot_dir.mkdir(parents=True, exist_ok=True)
        self.dl_dir.mkdir(exist_ok=True)
        self.events: list[dict] = []
        self.log = log

    def _screenshot(self, page: Page, seq: int) -> str:
        """전체 화면 스크린샷을 JPEG로 저장하고 상대 경로를 돌려준다."""
        rel = f"screenshots/{seq:03d}.jpg"
        try:
            page.wait_for_load_state("load", timeout=3000)
        except Exception:
            pass
        try:
            page.screenshot(path=str(self.out_dir / rel), type="jpeg",
                            quality=JPEG_QUALITY, full_page=True)
            return rel
        except Exception as e:  # 페이지가 닫히는 중이면 스크린샷 생략
            self.log(f"  (스크린샷 실패: {e.__class__.__name__})")
            return ""

    def add(self, page: Page, data: dict) -> None:
        """이벤트 하나를 기록한다.

        스크린샷을 찍는 동안 다른 이벤트 처리가 끼어들 수 있으므로,
        순번을 먼저 정하고 목록에 넣은 뒤 스크린샷을 찍는다.
        """
        if data.get("input_type") == "password":
            data["value"] = "***"   # 비밀번호는 어떤 경우에도 저장하지 않음
        event = {"seq": len(self.events) + 1,
                 "time": datetime.now().isoformat(timespec="milliseconds"), **data}
        event.pop("ts", None)
        seq = event["seq"]
        self.events.append(event)
        event["screenshot"] = self._screenshot(page, seq)
        desc = event.get("label") or event.get("text") or event.get("id") or event.get("url", "")
        value = f" = {event['value']}" if event.get("value") not in (None, "") else ""
        self.log(f"[{seq:03d}] {event['type']:<9} {desc}{value}")

    def on_navigate(self, page: Page, url: str) -> None:
        """페이지 이동 기록."""
        if url.startswith("about:"):
            return
        self.add(page, {"type": "navigate", "url": url, "title": _safe_title(page)})

    def on_download(self, page: Page, download) -> None:
        """다운로드 파일을 저장하고 파일명을 기록한다."""
        filename = download.suggested_filename
        try:
            download.save_as(str(self.dl_dir / filename))
        except Exception as e:
            self.log(f"  (다운로드 저장 실패: {e})")
        self.add(page, {"type": "download", "filename": filename,
                        "url": page.url, "title": _safe_title(page)})


def _safe_title(page: Page) -> str:
    """페이지 제목 (이동 중이면 빈 문자열)."""
    try:
        return page.title()
    except Exception:
        return ""


def record(url: str, name: str, headless: bool = False,
           script: Callable[[Page], None] | None = None,
           wait_stdin: bool = True, log: Callable[[str], None] = print) -> Path:
    """녹화를 실행하고 녹화 폴더 경로를 돌려준다.

    script를 주면 사람 대신 그 함수가 페이지를 조작하고, 끝나면 녹화를 종료한다(평가·테스트용).
    """
    started = datetime.now()
    rec_id = f"{safe_name(name)}_{started:%Y%m%d_%H%M%S}"
    out_dir = RECORDINGS_DIR / rec_id
    rec = Recorder(out_dir, log)
    stop = threading.Event()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(viewport=VIEWPORT, accept_downloads=True, locale="ko-KR")
        context.expose_binding("__recordEvent", lambda source, data: rec.add(source["page"], data))
        context.add_init_script(path=str(INJECT_JS))

        attached: set[int] = set()

        def attach(page: Page) -> None:
            """새 페이지(탭)에 이동·다운로드·닫힘 감지를 붙인다."""
            if id(page) in attached:
                return
            attached.add(id(page))
            page.on("framenavigated",
                    lambda frame: rec.on_navigate(page, frame.url) if frame == page.main_frame else None)
            page.on("download", lambda d: rec.on_download(page, d))
            page.on("close", lambda _: stop.set() if not context.pages else None)

        context.on("page", attach)
        browser.on("disconnected", lambda _: stop.set())
        page = context.new_page()
        attach(page)
        page.goto(url)

        if script is not None:
            script(page)
            page.wait_for_timeout(800)
            stop.set()
        else:
            log("녹화 중입니다. 업무를 시연하세요. 끝내려면 터미널에서 Enter를 누르거나 브라우저를 닫으세요.")
            if wait_stdin:
                def wait_enter():
                    try:
                        input()
                        stop.set()
                    except EOFError:   # 터미널 입력이 없는 실행(예: Streamlit) → 브라우저 닫기로만 종료
                        pass
                threading.Thread(target=wait_enter, daemon=True).start()
            while not stop.is_set():
                pages = [pg for pg in context.pages if not pg.is_closed()]
                if not pages:
                    break
                try:
                    pages[0].wait_for_timeout(200)   # Playwright 이벤트 처리를 위해 대기
                except Exception:
                    break
        try:
            browser.close()
        except Exception:
            pass

    meta = {
        "id": rec_id,
        "name": name,
        "start_url": url,
        "started_at": started.isoformat(timespec="seconds"),
        "ended_at": datetime.now().isoformat(timespec="seconds"),
        "viewport": VIEWPORT,
        "event_count": len(rec.events),
        "events": rec.events,
    }
    (out_dir / "events.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"녹화 종료: 이벤트 {len(rec.events)}개 → {out_dir / 'events.json'}")
    return out_dir


def main() -> None:
    """명령줄 실행."""
    parser = argparse.ArgumentParser(description="브라우저 업무 녹화기")
    parser.add_argument("--url", default="http://localhost:5000", help="시작 주소")
    parser.add_argument("--name", required=True, help="녹화 이름 (예: 위탁업체점검조회)")
    args = parser.parse_args()
    record(args.url, args.name)


if __name__ == "__main__":
    main()
