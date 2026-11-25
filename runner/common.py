"""재생(v1)과 매크로 실행(v3+)이 함께 쓰는 도구: 요소 찾기, 실패 감지, 결과 저장."""
import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Locator, Page

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"
VIEWPORT = {"width": 1280, "height": 800}
FIND_TIMEOUT_MS = 5000

# 실행을 멈추는 원인 코드 → 사람이 읽는 설명
STOP_REASONS = {
    "element_not_found": "화면에서 대상 요소를 찾지 못했습니다. 화면 구성이 바뀌었거나 이전 단계가 실패했을 수 있습니다.",
    "unexpected_page": "예상과 다른 페이지로 이동했습니다. 로그인 만료나 오류 페이지일 수 있습니다.",
    "no_result": "조회 결과가 0건입니다. 조회 조건(날짜·구분)이 맞는지 확인하세요.",
    "error_message": "화면에 오류 메시지가 표시되었습니다.",
    "download_missing": "다운로드가 일어나지 않았습니다.",
    "user_declined": "사용자가 실행을 취소했습니다.",
    "action_failed": "동작 실행 중 오류가 났습니다.",
    "file_missing": "작업할 파일을 찾지 못했습니다. 파일 경로를 확인하세요.",
    "structure_mismatch": "엑셀 파일 형식(시트·머리글)이 녹화 때와 다릅니다. 같은 양식의 파일인지 확인하세요.",
    "excel_not_found": "엑셀 파일에서 대상(시트·열·행)을 찾지 못했습니다.",
    "formula_error": "수식 결과에 오류(#DIV/0!, #VALUE! 등)가 생겼습니다.",
}
HUMAN_HANDOFF = "자동 실행을 멈췄습니다. 이 단계부터는 사람이 직접 처리하세요."


def url_path(url: str) -> str:
    """URL에서 경로만 뽑는다 (예: http://x/a?b=1 → /a)."""
    return urlparse(url).path or "/"


def new_run_dir(source_id: str) -> Path:
    """runs/<원본id>_<타임스탬프>/ 폴더를 만든다."""
    d = RUNS_DIR / f"{source_id}_{datetime.now():%Y%m%d_%H%M%S}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _unique(loc: Locator) -> Locator | None:
    """정확히 하나만 보이면 그 요소를 돌려준다."""
    try:
        if loc.count() == 1:
            return loc.first
        visible = [loc.nth(i) for i in range(loc.count()) if loc.nth(i).is_visible()]
        return visible[0] if len(visible) == 1 else None
    except Exception:
        return None


def find_element(page: Page, locator: dict, strategies=("id", "label", "text", "selector"),
                 timeout_ms: int = FIND_TIMEOUT_MS) -> tuple[Locator | None, str]:
    """요소를 id → label 텍스트 → 버튼 텍스트 → CSS 선택자 순으로 찾는다.

    돌려주는 값: (찾은 요소, 사용한 방법). 못 찾으면 (None, "").
    """
    tag = locator.get("tag", "")
    input_type = locator.get("input_type", "")
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        for s in strategies:
            loc = None
            if s == "id" and locator.get("id"):
                loc = page.locator(f'[id="{locator["id"]}"]')
            elif s == "label" and locator.get("label") and tag in ("input", "select", "textarea"):
                loc = page.get_by_label(locator["label"], exact=True)
                if input_type in ("radio", "checkbox") and locator.get("name"):
                    loc = loc.and_(page.locator(f'[name="{locator["name"]}"]'))
            elif s == "text" and locator.get("text") and tag in ("button", "a", "input"):
                role = "link" if tag == "a" else "button"
                loc = page.get_by_role(role, name=locator["text"], exact=True)
            elif s == "selector" and locator.get("selector"):
                loc = page.locator(locator["selector"])
            if loc is not None:
                found = _unique(loc)
                if found is not None:
                    return found, s
        if time.monotonic() > deadline:
            return None, ""
        page.wait_for_timeout(200)


def settle(page: Page) -> None:
    """동작 후 페이지 이동·로딩이 끝날 때까지 잠깐 기다린다."""
    try:
        page.wait_for_load_state("load", timeout=10000)
    except Exception:
        pass
    page.wait_for_timeout(150)


def visible_text(page: Page, selector: str) -> str:
    """선택자에 해당하는 요소가 보이면 그 텍스트, 아니면 빈 문자열."""
    try:
        loc = page.locator(selector)
        if loc.count() and loc.first.is_visible():
            # 알림 앞의 아이콘 글자(✓, !)는 빼고 문구만
            return re.sub(r"^[✓!\s]+", "", loc.first.inner_text()).strip()
    except Exception:
        pass
    return ""


def detect_problem(page: Page) -> tuple[str, str] | None:
    """화면에 '결과 없음'이나 오류 메시지가 보이면 (원인 코드, 화면 문구)를 돌려준다."""
    msg = visible_text(page, "#error-message, .alert-error")
    if msg:
        return "error_message", msg
    msg = visible_text(page, "#no-result")
    if msg:
        return "no_result", msg
    try:
        body = page.inner_text("body")
    except Exception:
        return None
    if re.search(r"조회 결과가 없습니다|조회 결과 0건", body):
        return "no_result", "조회 결과가 없습니다"
    return None


def page_messages(page: Page) -> list[str]:
    """저장 완료 같은 안내 메시지를 모은다."""
    msg = visible_text(page, "#save-message, .alert-success")
    return [msg] if msg else []


def fail_screenshot(page: Page, run_dir: Path, step_no: int) -> str:
    """실패한 화면을 저장하고 상대 경로를 돌려준다."""
    rel = f"fail_step{step_no:03d}.jpg"
    try:
        page.screenshot(path=str(run_dir / rel), type="jpeg", quality=70, full_page=True)
        return rel
    except Exception:
        return ""


def save_result(run_dir: Path, result: dict) -> Path:
    """result.json으로 저장한다 (평가 스크립트가 읽는 형식)."""
    path = run_dir / "result.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def new_result(mode: str, source: str, run_dir: Path) -> dict:
    """결과 기본 틀. replay와 macro가 같은 형식을 쓴다."""
    return {
        "mode": mode,                  # "replay"(v1) / "macro"(v3, v4)
        "source": source,              # 녹화 폴더 또는 매크로 파일
        "run_dir": str(run_dir),
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": None,
        "variables": {},
        "total": 0,
        "succeeded": 0,
        "success": False,
        "stopped_at": None,            # 멈춘 단계 번호
        "stop_reason": None,           # STOP_REASONS의 키
        "stop_message": None,
        "steps": [],                   # [{step, action, target, status, message, ...}]
        "downloads": [],               # 다운로드된 파일명
        "messages": [],                # 저장 완료 등 화면 메시지
    }


def finish_result(result: dict) -> dict:
    """성공 수와 종료 시각을 채운다."""
    result["succeeded"] = sum(1 for s in result["steps"] if s["status"] in ("성공", "건너뜀"))
    result["success"] = result["stopped_at"] is None and result["succeeded"] == result["total"]
    result["finished_at"] = datetime.now().isoformat(timespec="seconds")
    return result
