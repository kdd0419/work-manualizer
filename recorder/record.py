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
import base64
import json
import re
import shutil
import threading
import time
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
        self._cdp: dict[int, object] = {}       # 페이지별 브라우저 캡처 연결
        self._last_url: dict[int, str] = {}     # 페이지별 마지막으로 기록한 주소 (중복 기록 방지)
        self._loaded: dict[int, str] = {}       # 페이지별 로딩이 끝난 주소
        self._nav_queue: list[list] = []        # [page, 이동 이벤트, 찍을 시각(None=로딩 전)]
        self._pre: dict[int, tuple[Path, float]] = {}   # 누르는 순간 미리 찍어 둔 화면 (페이지별)

    # ------------------------------------------------------------ 스크린샷 시점
    # 1) 클릭: 마우스를 '누르는 순간'(pointerdown) 미리 찍어 둔다. 클릭 순간에 찍으면 이미 페이지 이동이
    #    시작돼 캡처가 멈추거나 다음 화면이 찍혔다.
    # 2) 페이지 이동: 로딩이 끝나고 0.35초 뒤(화면이 다 그려진 뒤) 찍는다. 기다림은 이벤트 처리 안이 아니라
    #    녹화기의 바깥 반복(flush)에서 한다 — 이벤트 처리 안에서 기다리면 서로 기다리다 멈췄다.
    NAV_DELAY = 0.35
    PRE_VALID = 3.0     # 미리 찍은 화면을 그 뒤 클릭에 쓸 수 있는 시간(초)

    def on_prepare(self, page: Page) -> None:
        """마우스를 누르는(또는 Enter를 누르는) 순간: 지금 화면을 미리 찍어 둔다."""
        self._catch_up(page)
        path = self.shot_dir / f"_pre_{id(page)}.jpg"
        if self._cdp_shot(page, path):
            self._pre[id(page)] = (path, time.monotonic())

    def _take_pre(self, page: Page, seq: int) -> str:
        """미리 찍어 둔 화면이 있으면 이 이벤트의 스크린샷으로 쓴다."""
        pre = self._pre.pop(id(page), None)
        if not pre or time.monotonic() - pre[1] > self.PRE_VALID or not pre[0].exists():
            return ""
        rel = f"screenshots/{seq:03d}.jpg"
        pre[0].replace(self.out_dir / rel)
        return rel

    def _copy_pre(self, page: Page, seq: int) -> str:
        """누르는 순간 찍어 둔 화면을 (지우지 않고) 이 이벤트 번호로 복사한다.

        입력칸 값은 다른 곳을 누를 때 한꺼번에 기록되므로, 그 순간엔 이미 페이지 이동이 시작됐을 수 있다.
        """
        pre = self._pre.get(id(page))
        if not pre or time.monotonic() - pre[1] > self.PRE_VALID or not pre[0].exists():
            return ""
        rel = f"screenshots/{seq:03d}.jpg"
        shutil.copyfile(pre[0], self.out_dir / rel)
        return rel

    def _copy_prev(self, event: dict) -> str:
        """같은 주소에서 찍힌 바로 앞 이벤트의 스크린샷을 이 이벤트 번호로 복사한다."""
        for prev in reversed(self.events[:-1]):
            if prev.get("url") != event.get("url"):
                break
            src = self.out_dir / prev["screenshot"] if prev.get("screenshot") else None
            if src and src.exists():
                rel = f"screenshots/{event['seq']:03d}.jpg"
                shutil.copyfile(src, self.out_dir / rel)
                return rel
        return ""

    def _catch_up(self, page: Page) -> None:
        """사용자가 이 페이지에서 동작을 시작함 → 아직 못 찍은 이동 화면을 지금 찍는다 (곧 떠날 수 있으므로)."""
        for item in self._nav_queue:
            pg, ev, _ = item
            if pg is page and not ev.get("screenshot") and page.url == ev.get("url"):
                ev["screenshot"] = self._screenshot(page, ev["seq"])
        self._nav_queue = [it for it in self._nav_queue if not it[1].get("screenshot")]

    def flush(self, force: bool = False) -> None:
        """찍을 때가 된 페이지 이동 스크린샷을 찍는다 (녹화기 바깥 반복에서 주기적으로 부름)."""
        now = time.monotonic()
        keep = []
        for item in self._nav_queue:
            page, ev, ready = item
            if page.is_closed():
                continue
            if ready is None and self._loaded.get(id(page)) == ev.get("url"):
                ready = item[2] = now + self.NAV_DELAY
            if not force and (ready is None or now < ready):
                keep.append(item)
                continue
            if page.url == ev.get("url") or force:
                ev["screenshot"] = self._screenshot(page, ev["seq"])
            # 이미 다른 페이지로 넘어갔으면 중간 단계라 생략
        self._nav_queue = keep

    def _screenshot(self, page: Page, seq: int, rect: dict | None = None) -> str:
        """지금 보이는 화면을 JPEG로 저장하고 상대 경로를 돌려준다.

        페이지 전체(위~아래)가 아니라 보이는 화면만 찍는다. 외부 사이트의 긴 페이지를 통째로 찍으면
        시간이 오래 걸려 브라우저가 멈춘 것처럼 보이고, 고정 메뉴가 그림 중간에 끼는 문제가 있었다.
        rect(누른 요소 위치)가 있으면 빨간 상자로 표시한다.
        """
        rel = f"screenshots/{seq:03d}.jpg"
        path = self.out_dir / rel
        if not self._cdp_shot(page, path):
            try:   # 예비 방법 (글꼴 로딩을 기다리므로 느릴 수 있음)
                page.screenshot(path=str(path), type="jpeg", quality=JPEG_QUALITY, timeout=5000, caret="hide")
            except Exception as e:  # 페이지가 닫히는 중이면 스크린샷 생략
                self.log(f"  (스크린샷 실패: {e.__class__.__name__})")
                return ""
        if rect:
            _mark(path, rect)
        return rel

    def _cdp_shot(self, page: Page, path: Path) -> bool:
        """브라우저 자체 캡처(CDP)로 보이는 화면을 찍는다.

        Playwright의 page.screenshot은 웹 글꼴이 다 내려받아질 때까지 기다려서, 글꼴이 많은 외부 사이트나
        인터넷이 느릴 때 몇 초씩 멈췄다(그동안 녹화기가 다음 동작을 처리하지 못해 브라우저가 멈춘 것처럼 보임).
        """
        try:
            key = id(page)
            if key not in self._cdp:
                self._cdp[key] = page.context.new_cdp_session(page)
            res = self._cdp[key].send("Page.captureScreenshot",
                                      {"format": "jpeg", "quality": JPEG_QUALITY, "fromSurface": True})
            path.write_bytes(base64.b64decode(res["data"]))
            return True
        except Exception:
            self._cdp.pop(id(page), None)
            return False

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
        rect = event.pop("rect", None) if data.get("type") in ("click", "input", "change") else None
        event.pop("rect", None)
        seq = event["seq"]
        self.events.append(event)
        kind = data.get("type")
        event["screenshot"] = ""
        if kind == "navigate":
            self._nav_queue.append([page, event, None])   # 로딩이 끝나고 조금 뒤 flush에서 찍는다
        elif kind == "download" or (kind == "submit" and data.get("via") == "click"):
            pass   # 다운로드는 바로 앞 클릭 화면이, 버튼 제출은 그 버튼 클릭 화면이 단계 화면
        else:
            if kind in ("input", "change"):
                self._catch_up(page)
            if kind in ("click", "submit"):
                event["screenshot"] = self._take_pre(page, seq)
            if not event["screenshot"]:
                event["screenshot"] = self._screenshot(page, seq)
            if not event["screenshot"]:   # 이미 페이지 이동이 시작돼 못 찍음 → 누르는 순간 찍어 둔 화면 또는 바로 앞 화면
                event["screenshot"] = self._copy_pre(page, seq) or self._copy_prev(event)
            if rect and event["screenshot"]:
                _mark(self.out_dir / event["screenshot"], rect)
        desc = event.get("label") or event.get("text") or event.get("id") or event.get("url", "")
        value = f" = {event['value']}" if event.get("value") not in (None, "") else ""
        self.log(f"[{seq:03d}] {event['type']:<9} {desc}{value}")

    def on_navigate(self, page: Page, url: str) -> None:
        """페이지 이동 기록."""
        if url.startswith("about:") or self._last_url.get(id(page)) == url:
            return
        self._last_url[id(page)] = url
        self.add(page, {"type": "navigate", "url": url, "title": _safe_title(page)})

    def on_load(self, page: Page) -> None:
        """페이지 로딩이 끝났음을 기억한다 (스크린샷은 조금 뒤 flush에서)."""
        self._loaded[id(page)] = page.url

    def on_download(self, page: Page, download) -> None:
        """다운로드 파일을 저장하고 파일명을 기록한다."""
        filename = download.suggested_filename
        try:
            download.save_as(str(self.dl_dir / filename))
        except Exception as e:
            self.log(f"  (다운로드 저장 실패: {e})")
        self.add(page, {"type": "download", "filename": filename,
                        "url": page.url, "title": _safe_title(page)})


class _HumanPage:
    """자동 시연(평가·테스트)용 페이지: 클릭을 사람처럼 '페이지 안에서' 일으킨다.

    Playwright의 page.click은 클릭으로 시작된 페이지 이동이 끝날 때까지 기다리는데, 그동안 녹화기의
    스크린샷 요청이 처리되지 못해 서로 기다리며 멈췄다. 사람이 마우스로 누를 때는 이 문제가 없으므로
    시연 스크립트도 같은 방식으로 누르게 한다. 나머지 기능은 원래 페이지로 그대로 넘긴다.
    """

    def __init__(self, page: Page, rec: "Recorder"):
        self._page, self._rec = page, rec

    def __getattr__(self, name):
        attr = getattr(self._page, name)
        if not callable(attr):
            return attr

        def call(*a, **kw):
            self._rec.flush()
            if not self._page.is_closed():
                self._rec._catch_up(self._page)   # 다음 동작 전에 아직 못 찍은 이동 화면부터
            return attr(*a, **kw)
        return call

    def _idle(self, ms: int) -> None:
        """기다리는 동안 녹화기가 밀린 스크린샷을 찍게 한다."""
        for _ in range(max(1, ms // 100)):
            self._page.wait_for_timeout(100)
            self._rec.flush()

    def click(self, selector: str, **_):
        page = self._page
        loc = page.locator(selector).first
        loc.wait_for(state="visible", timeout=10000)
        before = page.url
        # 사람처럼: 누르기(pointerdown) → 잠깐 → 클릭
        loc.evaluate("el => el.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}))")
        self._idle(150)
        loc.evaluate("el => setTimeout(() => el.click(), 0)")
        for _ in range(15):               # 이동이 시작되는지 잠깐 지켜본다 (최대 1.5초)
            self._idle(100)
            if page.url != before:
                break
        try:
            page.wait_for_load_state("load", timeout=10000)
        except Exception:
            pass
        self._idle(500)                   # 이동한 화면 스크린샷이 찍힐 시간


def _mark(path: Path, rect: dict) -> None:
    """스크린샷에서 누른 요소 위치에 빨간 상자를 그린다 (화면 밖이면 그리지 않음)."""
    try:
        from PIL import Image, ImageDraw
        x, y, w, h = (float(rect.get(k, 0)) for k in ("x", "y", "w", "h"))
        with Image.open(path) as im:
            im = im.convert("RGB")
            if w <= 0 or h <= 0 or x > im.width or y > im.height or x + w < 0 or y + h < 0:
                return
            box = [max(x - 4, 1), max(y - 4, 1), min(x + w + 4, im.width - 2), min(y + h + 4, im.height - 2)]
            ImageDraw.Draw(im).rounded_rectangle(box, radius=6, outline=(239, 68, 68), width=3)
            im.save(path, "JPEG", quality=JPEG_QUALITY)
    except Exception:
        pass


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
        context.expose_binding("__recordPrep", lambda source: rec.on_prepare(source["page"]))
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
            page.on("load", lambda _: rec.on_load(page))
            page.on("close", lambda _: stop.set() if not context.pages else None)
            # 새 탭은 붙이기 전에 첫 페이지 이동(과 로딩)이 끝났을 수 있음 → 지금 주소를 바로 기록
            if page.url and not page.url.startswith("about:"):
                rec.on_navigate(page, page.url)
                try:
                    if page.evaluate("document.readyState") == "complete":
                        rec.on_load(page)
                except Exception:
                    pass

        context.on("page", attach)
        browser.on("disconnected", lambda _: stop.set())
        page = context.new_page()
        attach(page)
        page.goto(url)

        if script is not None:
            human = _HumanPage(page, rec)
            script(human)
            human._idle(800)
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
                    pages[0].wait_for_timeout(100)   # Playwright 이벤트 처리를 위해 대기
                    rec.flush()                      # 찍을 때가 된 페이지 이동 스크린샷
                except Exception:
                    break
        try:
            rec.flush(force=True)                    # 끝나기 전에 남은 스크린샷
        except Exception:
            pass
        for f in rec.shot_dir.glob("_pre_*.jpg"):    # 쓰이지 않은 미리 찍은 화면 정리
            f.unlink(missing_ok=True)
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
