"""시작 화면의 시연 영상 2개를 만든다 (숨김 브라우저로 화면을 녹화, 창이 뜨지 않음).

실행: python -m tools.make_demo_videos [site|excel]   (관리 서버가 켜져 있어야 함: start.bat)
결과: app/static/videos/site_demo.webm, excel_demo.webm (+ 미리보기 .jpg)

- 사이트 영상: 가상 포털 전자출석 시연 → 해석 → 매뉴얼 → 과목만 바꿔 자동 실행 → 같은 과목이면 멈춤
- Excel 영상: 엑셀 녹화 화면(실제 Excel 캡처) → 규칙으로 해석 → 11월 파일로 실행 → 결과 파일 미리보기
모두 가상 시스템·샘플 데이터만 쓴다. 시작 전에 가상 데이터를 오늘 기준으로 초기화한다(출석 기록 비움).
"""
import base64
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from analyzer.josa import fix_josa  # noqa: E402
OUT = ROOT / "app" / "static" / "videos"
ADMIN = "http://localhost:8000"
PORTAL = "http://localhost:5000/portal/login"
SIZE = {"width": 1280, "height": 720}       # 영상 크기
VIEW = {"width": 1440, "height": 810}       # 화면 크기 (조금 넓게 잡아 표가 좁아지지 않게, 영상은 줄여서 저장)
SITE_REC = "포털_전자출석_"
EXCEL_REC = "엑셀_점검부적합정리_"

# 모든 페이지에 붙는 자막·클릭 표시
OVERLAY_JS = r"""
(() => {
  const css = `
  #__cap{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);z-index:2147483647;max-width:86%;
    padding:14px 26px;border-radius:16px;background:rgba(17,24,39,.88);color:#fff;font:600 20px/1.45 'Pretendard','Malgun Gothic',sans-serif;
    box-shadow:0 12px 36px rgba(0,0,0,.35);text-align:center;transition:opacity .35s, transform .35s;backdrop-filter:blur(6px)}
  #__cap small{display:block;margin-top:4px;font:500 14.5px/1.4 'Pretendard','Malgun Gothic',sans-serif;color:#c7d2fe}
  #__cap b{color:#a5f3fc}
  #__cap.hide{opacity:0;transform:translate(-50%,12px)}
  .__ring{position:fixed;z-index:2147483646;pointer-events:none;border:3px solid #ef4444;border-radius:10px;
    box-shadow:0 0 0 6px rgba(239,68,68,.25);animation:__pulse .9s ease-out}
  @keyframes __pulse{0%{transform:scale(1.25);opacity:0}40%{opacity:1}100%{transform:scale(1)}}`;
  // 주소 없는 페이지(set_content)에서는 저장소 접근이 막히므로 실패해도 무시
  const store = { get: () => { try { return sessionStorage.getItem('__cap'); } catch (e) { return null; } },
                  set: (v) => { try { v ? sessionStorage.setItem('__cap', v) : sessionStorage.removeItem('__cap'); } catch (e) {} } };
  const add = () => {
    if (document.getElementById('__cap')) return;
    const st = document.createElement('style'); st.textContent = css; document.documentElement.appendChild(st);
    const c = document.createElement('div'); c.id = '__cap'; c.className = 'hide'; document.documentElement.appendChild(c);
    const saved = store.get();
    if (saved) { c.innerHTML = saved; c.className = ''; }
  };
  window.__cap = (html) => { add(); const c = document.getElementById('__cap');
    if (!html) { c.className = 'hide'; store.set(null); return; }
    c.innerHTML = html; c.className = ''; store.set(html); };
  window.__ring = (el) => { if (!el) return; const r = el.getBoundingClientRect(); const d = document.createElement('div');
    d.className = '__ring'; Object.assign(d.style, {left: (r.left - 6) + 'px', top: (r.top - 6) + 'px', width: (r.width + 12) + 'px', height: (r.height + 12) + 'px'});
    document.documentElement.appendChild(d); setTimeout(() => d.remove(), 1100); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
})();
"""

SLIDE_CSS = """
body{margin:0;height:100vh;display:grid;place-items:center;overflow:hidden;font-family:'Pretendard','Malgun Gothic',sans-serif;color:#fff;
  background:radial-gradient(900px 400px at 10% 0%,rgba(167,139,250,.55),transparent 60%),radial-gradient(700px 300px at 100% 100%,rgba(34,211,238,.35),transparent 60%),
  linear-gradient(135deg,#1e1b4b,#312e81 55%,#4338ca)}
.wrap{text-align:center;animation:in .8s ease-out}
.badge{display:inline-block;padding:6px 14px;border-radius:999px;background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.25);font-weight:700;font-size:16px;color:#e0e7ff}
h1{font-size:56px;margin:22px 0 14px;letter-spacing:-.03em}
h1 span{background:linear-gradient(90deg,#a5f3fc,#c4b5fd,#f0abfc);-webkit-background-clip:text;background-clip:text;color:transparent}
p{font-size:22px;color:#e0e7ff;margin:0}
.flow{display:flex;gap:14px;justify-content:center;margin-top:30px}
.flow div{padding:12px 20px;border-radius:14px;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);font-size:19px;font-weight:700;opacity:0;animation:in .6s ease-out forwards}
@keyframes in{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
"""


def slide(page: Page, badge: str, title: str, sub: str, flow: list[str] | None = None, secs: float = 4.0) -> None:
    """제목·끝 화면."""
    items = "".join(f'<div style="animation-delay:{0.5 + i * 0.35:.2f}s">{f}</div>' for i, f in enumerate(flow or []))
    page.set_content(f"<html><head><meta charset='utf-8'><style>{SLIDE_CSS}</style></head><body><div class='wrap'>"
                     f"<span class='badge'>{badge}</span><h1>{title}</h1><p>{sub}</p>"
                     f"{f'<div class=flow>{items}</div>' if items else ''}</div></body></html>")
    page.wait_for_timeout(int(secs * 1000))


def cap(page: Page, html: str | None, wait: float = 0.0) -> None:
    """자막 바꾸기 (html=None이면 숨김)."""
    page.evaluate("h => window.__cap && window.__cap(h)", html)
    if wait:
        page.wait_for_timeout(int(wait * 1000))


def click(page: Page, selector: str, wait: float = 0.9) -> None:
    """누를 곳을 빨간 테두리로 보여 준 뒤 누른다."""
    loc = page.locator(selector).first
    loc.scroll_into_view_if_needed()
    loc.evaluate("el => window.__ring && window.__ring(el)")
    page.wait_for_timeout(650)
    loc.click()
    page.wait_for_timeout(int(wait * 1000))


def typing(page: Page, selector: str, text: str) -> None:
    loc = page.locator(selector).first
    loc.evaluate("el => window.__ring && window.__ring(el)")
    loc.click()
    loc.press_sequentially(text, delay=85)
    page.wait_for_timeout(350)


def smooth_scroll(page: Page, selector: str, total: int, steps: int = 12, pause: int = 140) -> None:
    for _ in range(steps):
        page.evaluate("([s, d]) => { const el = document.querySelector(s) || document.scrollingElement; "
                      "(el === document.body ? window : document.scrollingElement).scrollBy({top: d}); }", [selector, total // steps])
        page.wait_for_timeout(pause)


def wait_badge(page: Page, prefix: str, timeout: float = 240) -> str:
    t = time.time()
    while time.time() - t < timeout:
        txt = page.inner_text(f"#{prefix}-badge")
        if txt in ("완료", "실패", "오류"):
            return txt
        page.wait_for_timeout(400)
    return "시간초과"


def rec_id(prefix: str) -> str:
    return next(d.name for d in sorted((ROOT / "recordings").iterdir()) if d.name.startswith(prefix))


def open_admin(page: Page, tab: str) -> None:
    """관리 화면을 원하는 탭으로 바로 연다 (다른 탭이 잠깐 보이지 않게, 열리기 전에 기억값을 넣어 둠)."""
    page.add_init_script(f"if (location.port === '8000') {{ localStorage.setItem('skipGuide','1'); localStorage.setItem('tab','{tab}'); }}")
    page.goto(ADMIN)
    page.wait_for_timeout(1500)


def poster(page: Page, name: str) -> None:
    """영상 카드에 쓸 미리보기 그림 (영상과 같은 16:9)."""
    from PIL import Image
    p = OUT / f"{name}.jpg"
    page.screenshot(path=str(p), type="jpeg", quality=85)
    with Image.open(p) as im:
        im.convert("RGB").resize((SIZE["width"], SIZE["height"])).save(p, "JPEG", quality=82)


# ------------------------------------------------------------------ 사이트 영상

def site_video(page: Page) -> None:
    rid = rec_id(SITE_REC)
    slide(page, "업무 녹화 매크로 AI · 시연 1", "🌐 <span>사이트 업무</span>",
          "가상 대학 포털 ‘전자출석’을 한 번 보여 주면", ["🎬 녹화", "🧠 해석", "📘 매뉴얼", "⚡ 자동 실행"], 4.5)

    # ① 녹화: 평소처럼 업무를 한 번
    page.goto(PORTAL)
    cap(page, "<b>① 녹화</b> — 평소처럼 업무를 한 번 해 보입니다<small>녹화 시작 후 가상 포털에서 전자출석</small>", 1.6)
    typing(page, "#portal-id", "2026123456")
    typing(page, "#portal-password", "pw1234")
    click(page, "#portal-login-button", 1.4)
    click(page, "#menu-attendance", 1.3)
    page.locator("#course-select").evaluate("el => window.__ring(el)")
    page.wait_for_timeout(500)
    page.select_option("#course-select", label="웹프로그래밍 (이샘플)")
    page.wait_for_timeout(700)
    typing(page, "#attendance-code", "4821")
    poster(page, "site_demo")   # 카드 미리보기: 포털 전자출석 화면
    click(page, "#attendance-submit", 1.0)
    cap(page, "<b>① 녹화</b> — 출석 완료! 이제 녹화 창을 닫으면 저장됩니다<small>누른 곳·입력값·화면이 단계별로 기록됨</small>", 2.6)

    # ② 해석
    open_admin(page, "analyze")
    page.select_option("#rec-select", rid)
    page.wait_for_timeout(1300)
    cap(page, "<b>② 해석</b> — 녹화가 사람이 읽는 단계로 정리됩니다<small>표에서 단계를 누르면 그때 화면(누른 곳은 빨간 상자)</small>", 1.5)
    rows = page.locator("#steps-table tbody tr")
    for i in (3, 5, 6, 7):
        if i < rows.count():
            rows.nth(i).evaluate("el => window.__ring(el)")
            rows.nth(i).click()
            page.wait_for_timeout(1300)
    page.locator("#vars-area").scroll_into_view_if_needed()
    cap(page, "<b>② 해석</b> — 다음 실행 때 바뀔 값은 <b>변수</b>로<small>비밀번호는 매번 입력 · 과목·출석코드는 바꿀 수 있음</small>", 3.0)

    # ③ 매뉴얼
    page.click('#tabs button[data-tab="manual"]')
    page.wait_for_timeout(1500)
    page.locator("#manual-area").scroll_into_view_if_needed()
    cap(page, "<b>③ 매뉴얼</b> — 스크린샷이 들어간 업무 매뉴얼<small>부분 수정·삭제·되돌리기, PDF로 받기</small>", 1.0)
    blk = page.locator("#manual-view .mblock").nth(4)
    if blk.count():
        blk.hover()
        page.wait_for_timeout(1500)
    smooth_scroll(page, "main", 1500, 14, 170)
    page.wait_for_timeout(800)

    # ④ 자동 실행: 과목만 바꿔서
    page.click('#tabs button[data-tab="run"]')
    page.wait_for_timeout(1500)
    page.select_option("#macro-select", rid)
    page.wait_for_timeout(1200)
    page.evaluate("window.scrollTo(0, 0)")
    cap(page, "<b>④ 자동 실행</b> — 값만 바꿔서 실행합니다<small>이번에는 <b>인공지능개론</b> 과목으로</small>", 1.2)
    typing(page, '#var-form input[data-var="비밀번호"]', "pw1234")
    course = page.locator('#var-form input[data-var="과목"]')
    course.evaluate("el => window.__ring(el)")
    course.fill("")
    course.press_sequentially("인공지능개론 (박예시)", delay=70)
    page.wait_for_timeout(500)
    page.check("#opt-allow")
    page.check("#opt-headless")
    page.wait_for_timeout(600)
    click(page, "#btn-run", 0.5)
    cap(page, "<b>④ 자동 실행</b> — 녹화한 순서대로 알아서 진행 중…", 0)
    wait_badge(page, "run")
    page.wait_for_timeout(600)
    page.locator("#run-result").scroll_into_view_if_needed()
    cap(page, "<b>④ 자동 실행</b> — ✅ 출석 완료<small>사람이 한 것과 같은 결과</small>", 3.2)

    # ⑤ 같은 과목으로 다시 → 멈춤
    page.evaluate("window.scrollTo(0, 0)")
    cap(page, "<b>⑤ 문제가 생기면?</b> — 이미 출석한 과목으로 한 번 더", 1.0)
    course.fill("")
    course.press_sequentially("웹프로그래밍 (이샘플)", delay=60)
    page.wait_for_timeout(400)
    click(page, "#btn-run", 0.5)
    wait_badge(page, "run")
    page.wait_for_timeout(600)
    page.locator("#run-result").scroll_into_view_if_needed()
    cap(page, "<b>⑤ 알아서 멈춤</b> — “이미 오늘 출석한 과목” 오류를 알아채고 사람에게 넘깁니다", 3.8)
    cap(page, None)
    slide(page, "정리", "녹화 <span>한 번</span>이면 충분",
          "매뉴얼은 자동으로, 반복 업무는 매크로로", ["📘 매뉴얼·PDF", "⚡ 값만 바꿔 실행", "🛟 문제 시 멈춤"], 4.5)


# ------------------------------------------------------------------ Excel 영상

def excel_recording_slides(rid: str) -> str:
    """엑셀 녹화 화면(실제 Excel 캡처)을 자막과 함께 넘겨 보는 HTML."""
    d = ROOT / "recordings" / rid
    steps = json.loads((d / "steps.json").read_text(encoding="utf-8"))["steps"]
    slides = []
    for s in steps:
        p = d / s["screenshot"] if s.get("screenshot") else None
        if not p or not p.exists() or s["action"] == "저장":
            continue
        img = base64.b64encode(p.read_bytes()).decode()
        slides.append((img, f"{s['step']}. {fix_josa(s['description'])}"))
    items = "".join(f'<figure data-cap="{json.dumps(c, ensure_ascii=False)[1:-1]}"><img src="data:image/jpeg;base64,{img}"></figure>'
                    for img, c in slides)
    return f"""<html><head><meta charset='utf-8'><style>
body{{margin:0;background:#0b0f1e;overflow:hidden;height:100vh}}
figure{{position:absolute;inset:0;margin:0;display:grid;place-items:center;opacity:0;transition:opacity .6s}}
figure.on{{opacity:1}}
figure img{{width:96%;border-radius:12px;box-shadow:0 20px 60px rgba(0,0,0,.6);transform:scale(1);transition:transform 3.2s ease-out}}
figure.on img{{transform:scale(1.06)}}
.tag{{position:fixed;top:22px;left:26px;padding:8px 16px;border-radius:12px;background:#22c55e;color:#fff;font:700 17px 'Malgun Gothic',sans-serif;z-index:5}}
</style></head><body><div class="tag">🎬 Excel에서 녹화 중</div>{items}</body></html>""", len(slides)


def excel_result_html(xlsx: Path) -> str:
    """결과 엑셀 파일을 표로 보여 주는 HTML."""
    from openpyxl import load_workbook
    ws = load_workbook(xlsx, data_only=True).active   # 수식 대신 Excel이 계산해 저장한 값
    rows = list(ws.iter_rows(values_only=True))
    head = "".join(f"<th>{h}</th>" for h in rows[0])
    body = ""
    for r in rows[1:]:
        cls = "total" if str(r[0]) == "합계" else ("bad" if "부적합" in [str(x) for x in r] else "")
        body += f"<tr class='{cls}'>" + "".join(f"<td>{'' if v is None else v}</td>" for v in r) + "</tr>"
    return f"""<html><head><meta charset='utf-8'><style>
body{{margin:0;background:#f4f6fb;font-family:'Malgun Gothic',sans-serif;padding:26px 34px}}
h2{{margin:0 0 4px;font-size:24px;color:#1e1b4b}} p{{margin:0 0 14px;color:#5b6478}}
table{{border-collapse:collapse;width:100%;background:#fff;font-size:13px;box-shadow:0 6px 20px rgba(30,40,90,.08)}}
th{{background:#1f4e78;color:#fff;padding:7px 8px;text-align:left}} td{{padding:5px 8px;border-bottom:1px solid #e5e8f0}}
tr.bad td{{background:#fff1f2}} tr.total td{{background:#ecfdf5;font-weight:700}}
</style></head><body><h2>📄 {xlsx.name}</h2><p>11월 파일(44행)에 녹화한 작업을 그대로 적용한 결과 — 원본 파일은 그대로</p>
<table><tr>{head}</tr>{body}</table></body></html>"""


def excel_video(page: Page) -> None:
    rid = rec_id(EXCEL_REC)
    slide(page, "업무 녹화 매크로 AI · 시연 2", "📊 <span>Excel 업무</span>",
          "10월 점검 파일로 한 번 보여 주면, 11월 파일도 똑같이", ["🎬 Excel 녹화", "🧠 규칙으로 해석", "⚡ 다른 파일에 실행"], 4.5)

    # ① 녹화 화면 (실제 Excel 캡처를 차례로)
    html, n = excel_recording_slides(rid)
    page.set_content(html)
    steps_caps = page.eval_on_selector_all("figure", "els => els.map(e => e.dataset.cap)")
    page.evaluate(OVERLAY_JS)
    for i in range(n):
        page.evaluate("i => document.querySelectorAll('figure').forEach((f, k) => f.classList.toggle('on', k === i))", i)
        cap(page, f"<b>① 녹화</b> — {steps_caps[i]}", 2.9)
        if i == 1:
            poster(page, "excel_demo")   # 카드 미리보기: Excel 녹화 화면

    # ② 해석
    open_admin(page, "analyze")
    page.select_option("#rec-select", rid)
    page.wait_for_timeout(1300)
    cap(page, "<b>② 해석</b> — 몇 번째 행이 아니라 <b>규칙</b>으로 정리됩니다<small>예: “점검결과가 ‘적합’인 행을 모두 삭제”</small>", 1.2)
    rows = page.locator("#steps-table tbody tr")
    for i in (1, 2, 4, 6):
        if i < rows.count():
            rows.nth(i).evaluate("el => window.__ring(el)")
            rows.nth(i).click()
            page.wait_for_timeout(1300)
    page.locator("#vars-area").scroll_into_view_if_needed()
    cap(page, "<b>② 해석</b> — 입력파일·필터값·담당자는 <b>변수</b><small>다음 달엔 파일만 바꾸면 됩니다</small>", 3.0)

    # ③ 11월 파일로 실행
    page.click('#tabs button[data-tab="run"]')
    page.wait_for_timeout(1500)
    page.select_option("#macro-select", rid)
    page.wait_for_timeout(1500)
    page.evaluate("window.scrollTo(0, 0)")
    cap(page, "<b>③ 실행</b> — 입력파일을 <b>11월 파일</b>로 바꿉니다<small>✓ 같은 양식인 파일이 위에 표시</small>", 1.2)
    sel = page.locator("#var-form select[data-var]").first
    sel.evaluate("el => window.__ring(el)")
    page.wait_for_timeout(600)
    sel.select_option("samples/위탁업체점검_2026-11.xlsx")
    page.wait_for_timeout(1600)
    page.check("#opt-headless")
    click(page, "#btn-run", 0.5)
    cap(page, "<b>③ 실행</b> — Excel이 녹화한 순서대로 작업 중…<small>원본은 복사본으로 작업, 결과는 새 파일</small>", 0)
    wait_badge(page, "run")
    page.wait_for_timeout(600)
    page.locator("#run-result").scroll_into_view_if_needed()
    cap(page, "<b>③ 실행</b> — ✅ 8단계 모두 성공 · 결과 파일 링크로 바로 받기", 3.2)

    # ④ 결과 파일
    run_dir = page.evaluate("() => [...document.querySelectorAll('#run-result a')].map(a => a.getAttribute('href'))")
    xlsx = None
    for href in run_dir:
        if href and href.endswith(".xlsx"):
            from urllib.parse import unquote
            xlsx = ROOT / "runs" / unquote(href.split("/files/runs/")[1])
    if xlsx and xlsx.exists():
        page.set_content(excel_result_html(xlsx))
        page.evaluate(OVERLAY_JS)
        cap(page, "<b>④ 결과</b> — 부적합 행엔 담당자, 적합 행은 삭제, 날짜순 정렬, 합계 행까지<small>행 수가 달라도 수식 범위가 자동으로 맞춰짐</small>", 1.5)
        smooth_scroll(page, "body", 600, 10, 220)
        page.wait_for_timeout(1500)
    cap(page, None)
    slide(page, "정리", "다음 달엔 <span>파일만 바꾸면</span> 끝",
          "같은 양식이면 행 수가 달라도 똑같이 처리", ["📊 Excel 그대로", "🔒 원본 보존", "🛟 양식 다르면 멈춤"], 4.5)


def record(kind: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / f"_tmp_{kind}"
    shutil.rmtree(tmp, ignore_errors=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport=VIEW, record_video_dir=str(tmp), record_video_size=SIZE, locale="ko-KR")
        ctx.add_init_script(OVERLAY_JS)
        page = ctx.new_page()
        (site_video if kind == "site" else excel_video)(page)
        video = page.video
        ctx.close()
        src = Path(video.path())
        browser.close()
    dest = OUT / f"{kind}_demo.webm"
    shutil.move(str(src), dest)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"{kind}: {dest} ({dest.stat().st_size // 1024}KB)")


def main() -> None:
    kinds = sys.argv[1:] or ["site", "excel"]
    if "site" in kinds:   # 출석 기록을 비우고 시작 (가상 데이터를 오늘 기준으로 초기화)
        subprocess.run([sys.executable, "-m", "mock_system.seed"], cwd=ROOT, check=True)
    for k in kinds:
        record(k)


if __name__ == "__main__":
    main()
