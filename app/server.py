"""관리 화면 서버 (Flask). 브라우저에서 녹화·해석·매뉴얼·매크로 실행·업무 연결을 모두 할 수 있다.

실행: python -m app.server   (또는 start.bat 더블클릭)
주소: http://localhost:8000

- 가상 업무 시스템(포트 5000)이 꺼져 있으면 자동으로 함께 켠다.
- 녹화·해석·매크로 실행 같은 오래 걸리는 작업은 별도 프로세스(작업)로 돌리고,
  화면은 /api/jobs/<id>를 주기적으로 조회해 진행 로그를 보여 준다.
"""
import atexit
import io
import itertools
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import markdown
from flask import Flask, abort, jsonify, request, send_file, send_from_directory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analyzer.common import MACROS_DIR, MANUALS_DIR, RECORDINGS_DIR, read_json  # noqa: E402
from analyzer.josa import fix_josa  # noqa: E402
import analyzer.llm_client as llm  # noqa: E402  (.env도 여기서 읽힘)
from analyzer.llm_client import model_name  # noqa: E402
from runner.dates import apply_rule  # noqa: E402

PORT = 8000
MOCK_URL = "http://localhost:5000"
RUNS_DIR = ROOT / "runs"
SAMPLES_DIR = ROOT / "samples"      # 함께 들어 있는 가상 엑셀 파일
UPLOADS_DIR = ROOT / "uploads"      # 관리 화면에서 올린 엑셀 파일 (GitHub에 올리지 않음)
EXCEL_EXTS = (".xlsx", ".xlsm", ".xls", ".csv")
FAVORITES_FILE = ROOT / "favorites.json"   # 녹화 대상 사이트 즐겨찾기 (개인 설정, GitHub에 올리지 않음)
DEFAULT_FAVORITES = [
    {"name": "🎓 새솔대학교 통합포털 (가상)", "url": f"{MOCK_URL}/portal/login"},
    {"name": "📈 가나다증권 업무시스템 (가상)", "url": f"{MOCK_URL}/login"},
    {"name": "🧭 가상 시스템 선택 화면", "url": MOCK_URL},
]
PY = sys.executable
CHILD_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.json.ensure_ascii = False

# ------------------------------------------------------------------ 가상 업무 시스템

_mock_proc: subprocess.Popen | None = None


def mock_alive() -> bool:
    """가상 업무 시스템이 응답하는지."""
    try:
        urllib.request.urlopen(f"{MOCK_URL}/login", timeout=1)
        return True
    except Exception:
        return False


def start_mock() -> None:
    """가상 업무 시스템을 자식 프로세스로 켠다 (이미 켜져 있으면 그대로)."""
    global _mock_proc
    if mock_alive() or (_mock_proc and _mock_proc.poll() is None):
        return
    _mock_proc = subprocess.Popen([PY, "-m", "mock_system.app", "--no-debug"], cwd=ROOT, env=CHILD_ENV,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@atexit.register
def uses_mock(macro_path: Path) -> bool:
    """매크로가 시험용 가상 시스템 주소를 쓰는지 (실제 사이트 매크로면 가상 시스템을 켜지 않는다)."""
    try:
        return macro_path.exists() and MOCK_URL in macro_path.read_text(encoding="utf-8")
    except Exception:
        return False


def _stop_mock() -> None:
    """관리 화면이 꺼질 때 직접 켠 가상 업무 시스템도 끈다."""
    if _mock_proc and _mock_proc.poll() is None:
        _mock_proc.terminate()


# ------------------------------------------------------------------ 백그라운드 작업

_jobs: dict[str, dict] = {}
_job_ids = itertools.count(1)


def _run_job(job: dict, cmd: list[str]) -> None:
    """하위 프로세스를 실행하며 출력 줄을 job['log']에 쌓는다."""
    try:
        proc = subprocess.Popen(cmd, cwd=ROOT, env=CHILD_ENV, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
        job["pid"] = proc.pid
        for line in proc.stdout:
            line = line.rstrip()
            m = re.match(r"RESULT_JSON=(.+)", line)
            if m:   # 매크로 실행 결과 파일 위치 (화면 로그에는 안 보이게)
                job["result"] = read_json(Path(m.group(1).strip()))
            elif line:
                job["log"].append(line)
        job["returncode"] = proc.wait()
        job["status"] = "done" if job["returncode"] == 0 else "failed"
    except Exception as e:  # 실행 자체가 안 된 경우
        job["log"].append(f"작업 실행 오류: {e}")
        job["status"] = "failed"
    job["ended"] = time.time()


def start_job(kind: str, cmd: list[str], meta: dict | None = None) -> dict:
    """작업을 시작하고 작업 정보를 돌려준다."""
    jid = str(next(_job_ids))
    job = {"id": jid, "kind": kind, "status": "running", "log": [], "result": None,
           "returncode": None, "started": time.time(), "meta": meta or {}}
    _jobs[jid] = job
    threading.Thread(target=_run_job, args=(job, cmd), daemon=True).start()
    return job


# ------------------------------------------------------------------ 도우미

def rec_dir_of(rid: str) -> Path:
    """녹화 id 검증 후 폴더 경로 (경로 조작 방지)."""
    d = RECORDINGS_DIR / rid
    if "/" in rid or "\\" in rid or ".." in rid or not (d / "events.json").exists():
        abort(404, "녹화를 찾을 수 없습니다")
    return d


def macro_path_of(mid: str) -> Path:
    """매크로 id 검증 후 파일 경로."""
    p = MACROS_DIR / f"{mid}.json"
    if "/" in mid or "\\" in mid or ".." in mid or not p.exists():
        abort(404, "매크로를 찾을 수 없습니다")
    return p


def md_to_html(md: str) -> str:
    """마크다운 → HTML. 매뉴얼의 스크린샷 상대 경로를 /files/ 주소로 바꾼다.

    lazy_ol=False: '5. …'처럼 중간 번호로 시작하는 목록도 그 번호를 유지 (매뉴얼을 조각으로 나눠 보여 줄 때 필요)
    """
    html = markdown.markdown(fix_josa(md), extensions=["tables", "sane_lists"], lazy_ol=False)
    return html.replace('src="../recordings/', 'src="/files/recordings/')


def split_manual(md: str) -> list[str]:
    """매뉴얼을 고칠 수 있는 조각으로 나눈다 (analyzer.manual.split_blocks와 같은 규칙)."""
    from analyzer.manual import split_blocks
    return split_blocks(md)


def manual_history_dir(rid: str) -> Path:
    return RECORDINGS_DIR / rid / "manual_history"


def manual_payload(rid: str) -> dict:
    """매뉴얼 화면용: 전체 HTML + 고칠 수 있는 조각 + 직접 수정 여부 + 이전 버전 수."""
    p = MANUALS_DIR / f"{rid}.md"
    if not p.exists():
        return {"exists": False}
    md = fix_josa(p.read_text(encoding="utf-8"))   # 예전 매뉴얼의 '을(를)'도 편집칸에서 다듬어 보이고, 저장하면 그대로 남음
    hist = sorted(manual_history_dir(rid).glob("*.md")) if manual_history_dir(rid).exists() else []
    blocks = split_manual(md)
    return {"exists": True, "markdown": md, "html": md_to_html(md),
            "blocks": [{"md": b, "html": md_to_html(b)} for b in blocks],
            "edited": (RECORDINGS_DIR / rid / "manual_edited.json").exists(),
            "history": len(hist)}


def excel_installed() -> bool:
    """이 PC에 Microsoft Excel이 설치되어 있는지 (엑셀 녹화·실행에 필요)."""
    try:
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Excel.Application\CurVer"))
        return True
    except Exception:
        return False


def excel_file_of(rel: str) -> Path:
    """samples/, uploads/, 매크로 실행 결과(runs/<실행>/) 안의 엑셀 파일만 허용 (경로 조작 방지)."""
    p = (ROOT / rel).resolve()
    ok_dir = any(p.parent == d.resolve() for d in (SAMPLES_DIR, UPLOADS_DIR)) \
        or p.parent.parent == RUNS_DIR.resolve()
    if not ok_dir or p.suffix.lower() not in EXCEL_EXTS or not p.is_file():
        abort(400, "samples/, uploads/ 폴더 또는 매크로 실행 결과의 엑셀 파일만 쓸 수 있습니다.")
    return p


def rec_summary(d: Path) -> dict:
    """녹화 목록용 요약."""
    ev = read_json(d / "events.json")
    return {
        "id": d.name, "name": ev.get("name", ""), "events": ev.get("event_count", 0),
        "kind": ev.get("kind", "web"), "source": ev.get("source_file") or ev.get("start_url", ""),
        "started_at": ev.get("started_at", ""),
        "interpreted": (d / "steps.json").exists(),
        "macro": (MACROS_DIR / f"{d.name}.json").exists(),
        "manual": (MANUALS_DIR / f"{d.name}.md").exists(),
        "is_eval": d.name.startswith("eval_"),
    }


# ------------------------------------------------------------------ 화면

@app.get("/")
def index():
    """관리 화면 (한 페이지)."""
    return send_from_directory(app.static_folder, "index.html")


@app.get("/files/<area>/<path:sub>")
def files(area: str, sub: str):
    """녹화 스크린샷·실행 결과 파일 제공 (recordings, runs 폴더만)."""
    base = {"recordings": RECORDINGS_DIR, "runs": RUNS_DIR}.get(area)
    if base is None:
        abort(404)
    return send_from_directory(base, sub)


# ------------------------------------------------------------------ API: 사용 안내 (실행안내.md)

GUIDE_FILE = ROOT / "실행안내.md"


@app.get("/api/guide")
def guide():
    """실행안내.md를 첫 소개 부분과 '## ' 섹션별로 나눠 HTML로 돌려준다 (시작 화면용)."""
    if not GUIDE_FILE.exists():
        return jsonify(intro="", sections=[])
    md = GUIDE_FILE.read_text(encoding="utf-8")
    parts = re.split(r"(?m)^## ", md)
    intro = re.sub(r"(?m)^# .*\n", "", parts[0]).strip()   # 맨 위 제목은 화면 머리에 따로 보여 줌
    sections = []
    for p in parts[1:]:
        title, _, body = p.partition("\n")
        sections.append({"title": title.strip(), "html": md_to_html(body)})
    return jsonify(intro=md_to_html(intro), sections=sections)


# ------------------------------------------------------------------ API: 상태

@app.get("/api/status")
def status():
    """가상 업무 시스템·AI 키 상태."""
    return jsonify(mock=mock_alive(), mock_url=MOCK_URL, excel=excel_installed(),
                   has_key=llm.is_configured(), model=model_name(),
                   provider=llm.provider_label(), external=llm.is_external())


# ------------------------------------------------------------------ API: 사이트 즐겨찾기

def load_favorites() -> list[dict]:
    """즐겨찾기 목록. 파일이 없으면 가상 시스템 3개가 기본값."""
    if FAVORITES_FILE.exists():
        try:
            data = read_json(FAVORITES_FILE)
            return [f for f in data if isinstance(f, dict) and f.get("url")]
        except Exception:
            pass
    return [dict(f) for f in DEFAULT_FAVORITES]


def save_favorites(items: list[dict]) -> None:
    FAVORITES_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/api/favorites")
def favorites():
    """녹화 대상 사이트 즐겨찾기 목록."""
    return jsonify(load_favorites())


@app.post("/api/favorites")
def favorite_add():
    """즐겨찾기 추가 (같은 주소가 있으면 이름만 바꿈)."""
    body = request.json or {}
    url = str(body.get("url", "")).strip()
    name = str(body.get("name", "")).strip()[:40]
    if not re.match(r"^https?://\S+$", url):
        return jsonify(error="http:// 또는 https:// 로 시작하는 주소를 입력하세요."), 400
    items = load_favorites()
    for f in items:
        if f["url"] == url:
            f["name"] = name or f["name"]
            break
    else:
        items.append({"name": name or url, "url": url})
    save_favorites(items)
    return jsonify(items)


def is_mock_url(url: str) -> bool:
    return url.startswith(MOCK_URL)


def check_site(url: str) -> dict:
    """이 PC에서 사이트에 접속되는지 확인 (응답이 오면 로그인 요구·권한 오류여도 '접속 가능').
    관리 서버는 사용자 PC에서 돌기 때문에, 여기서 되면 사용자 브라우저에서도 사내망 주소에 접속된다."""
    import ssl
    if is_mock_url(url) and not mock_alive():
        return {"url": url, "state": "off", "message": "시험용 가상 시스템 (꺼져 있음 — 열면 자동으로 켜짐)"}
    t = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (work-macro site check)"})
    try:
        with urllib.request.urlopen(req, timeout=4, context=ssl._create_unverified_context()) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code   # 401·403·404여도 서버는 응답함
    except Exception as e:
        reason = getattr(e, "reason", e)
        return {"url": url, "state": "fail", "message": f"접속 안 됨: {str(reason)[:120]} (사내망·VPN·주소를 확인하세요)"}
    ms = round((time.time() - t) * 1000)
    msg = f"접속 가능 · {ms}ms" + ("" if code < 400 else f" (응답 {code} — 로그인·권한이 필요할 수 있음)")
    return {"url": url, "state": "ok", "message": msg, "ms": ms}


@app.post("/api/favorites/check")
def favorite_check():
    """등록한 사이트들의 접속 여부를 한꺼번에 확인 (동시에, 사이트당 최대 4초)."""
    from concurrent.futures import ThreadPoolExecutor
    urls = [f["url"] for f in load_favorites()]
    with ThreadPoolExecutor(max_workers=8) as ex:
        return jsonify(list(ex.map(check_site, urls)))


@app.post("/api/favorites/delete")
def favorite_delete():
    """즐겨찾기 삭제."""
    url = str((request.json or {}).get("url", "")).strip()
    items = [f for f in load_favorites() if f["url"] != url]
    save_favorites(items)
    return jsonify(items)


# ------------------------------------------------------------------ API: 엑셀 파일

@app.get("/api/excel/files")
def excel_files():
    """녹화·실행에 쓸 수 있는 엑셀 파일 (가상 샘플 + 올린 파일)."""
    items = []
    for group, d in (("샘플", SAMPLES_DIR), ("올린 파일", UPLOADS_DIR)):
        if d.exists():
            for p in sorted(d.iterdir()):
                if p.suffix.lower() in EXCEL_EXTS and not p.name.startswith("~$"):
                    items.append({"path": p.relative_to(ROOT).as_posix(), "name": p.name, "group": group,
                                  "size": p.stat().st_size})
    return jsonify(items)


def file_headers(p: Path) -> list[str] | None:
    """엑셀/CSV 파일 첫 시트의 머리글(1행). 읽을 수 없으면 None (Excel 없이 확인하는 용도)."""
    try:
        if p.suffix.lower() == ".csv":
            import csv
            with p.open(encoding="utf-8-sig", errors="replace", newline="") as f:
                return [c.strip() for c in next(csv.reader(f), [])]
        if p.suffix.lower() in (".xlsx", ".xlsm"):
            from openpyxl import load_workbook
            wb = load_workbook(p, read_only=True, data_only=True)
            try:
                row = next(wb.worksheets[0].iter_rows(min_row=1, max_row=1, values_only=True), ())
                return [str(c).strip() if c is not None else "" for c in row]
            finally:
                wb.close()
    except Exception:
        pass
    return None


@app.get("/api/macros/<mid>/files")
def macro_files(mid: str):
    """엑셀 매크로의 입력 파일 후보 + 녹화 때 파일과 양식(머리글)이 같은지. 같은 양식을 먼저."""
    m = read_json(macro_path_of(mid))
    want: list[str] = []
    for s in m["steps"]:
        if s.get("excel", {}).get("op") == "open":
            sheets = s["excel"].get("sheets", {})
            first = next(iter(sheets.values()), {})
            want = [str(h).strip() for h in first.get("headers", [])]
    items = excel_files().json
    for it in items:
        have = file_headers(ROOT / it["path"])
        it["match"] = None if have is None or not want else have[:len(want)] == want
    items.sort(key=lambda it: {True: 0, None: 1, False: 2}[it["match"]])
    return jsonify(items)


@app.post("/api/excel/upload")
def excel_upload():
    """엑셀 파일 올리기 → uploads/ 에 저장 (같은 이름이 있으면 번호를 붙임)."""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="파일을 고르세요."), 400
    name = re.sub(r'[\\/:*?"<>|]+', "_", Path(f.filename).name).strip() or "upload.xlsx"
    if Path(name).suffix.lower() not in EXCEL_EXTS:
        return jsonify(error=f"엑셀 파일만 올릴 수 있습니다 ({', '.join(EXCEL_EXTS)})."), 400
    UPLOADS_DIR.mkdir(exist_ok=True)
    dest, i = UPLOADS_DIR / name, 2
    while dest.exists():
        dest, i = UPLOADS_DIR / f"{Path(name).stem} ({i}){Path(name).suffix}", i + 1
    f.save(dest)
    return jsonify(path=dest.relative_to(ROOT).as_posix(), name=dest.name)


@app.post("/api/mock/start")
def mock_start():
    """가상 업무 시스템 켜기."""
    start_mock()
    for _ in range(20):
        if mock_alive():
            return jsonify(ok=True)
        time.sleep(0.3)
    return jsonify(ok=False, error="가상 업무 시스템을 켜지 못했습니다."), 500


# ------------------------------------------------------------------ API: 녹화·해석·매뉴얼

@app.get("/api/recordings")
def recordings():
    """녹화 목록 (최신 먼저)."""
    if not RECORDINGS_DIR.exists():
        return jsonify([])
    dirs = sorted((d for d in RECORDINGS_DIR.iterdir() if (d / "events.json").exists()),
                  key=lambda d: d.stat().st_mtime, reverse=True)
    return jsonify([rec_summary(d) for d in dirs])


@app.get("/api/recordings/<rid>")
def recording_detail(rid: str):
    """녹화 하나의 해석 단계·매크로·질문·답변."""
    d = rec_dir_of(rid)
    out = rec_summary(d)
    out["steps"] = read_json(d / "steps.json") if (d / "steps.json").exists() else None
    if out["steps"]:   # 예전에 만든 해석도 조사 표기를 다듬어 보여 준다
        for s in out["steps"]["steps"]:
            s["description"] = fix_josa(s.get("description", ""))
    out["overview"] = read_json(d / "overview.json") if (d / "overview.json").exists() else None
    mp = MACROS_DIR / f"{rid}.json"
    out["macro_data"] = read_json(mp) if mp.exists() else None
    qs = read_json(d / "questions.json")["questions"] if (d / "questions.json").exists() else []
    ans = {a["id"]: a["answer"] for a in read_json(d / "answers.json")["answers"]} \
        if (d / "answers.json").exists() else {}
    out["questions"] = [{**q, "question": fix_josa(q["question"]), "why": fix_josa(q.get("why", "")),
                         "answer": ans.get(q["id"], "")} for q in qs]
    return jsonify(out)


@app.post("/api/recordings/<rid>/answers")
def save_answers(rid: str):
    """판단 기준 질문 답변 저장."""
    from analyzer.questions import save_answers as _save
    rec_dir_of(rid)
    _save(rid, request.json.get("answers", {}))
    return jsonify(ok=True)


@app.get("/api/manuals/<rid>")
def manual(rid: str):
    """매뉴얼 미리보기 (조각별 HTML 포함)."""
    rec_dir_of(rid)
    return jsonify(manual_payload(rid))


@app.put("/api/manuals/<rid>")
def manual_save(rid: str):
    """직접 고친 매뉴얼 저장. 저장 전 내용은 이전 버전으로 보관한다."""
    from analyzer.manual import backup_manual
    rec_dir_of(rid)
    md = str((request.json or {}).get("markdown", ""))
    if not md.strip():
        return jsonify(error="빈 매뉴얼은 저장할 수 없습니다."), 400
    p = MANUALS_DIR / f"{rid}.md"
    backup_manual(rid, "직접 수정 전")
    p.write_text(md if md.endswith("\n") else md + "\n", encoding="utf-8")
    (RECORDINGS_DIR / rid / "manual_edited.json").write_text(
        json.dumps({"edited_at": time.strftime("%Y-%m-%dT%H:%M:%S")}), encoding="utf-8")
    return jsonify(manual_payload(rid))


IMAGE_EXTS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
MAX_IMAGE_MB = 10


@app.post("/api/manuals/<rid>/images")
def manual_image_upload(rid: str):
    """매뉴얼에 넣을 이미지 올리기 (끌어다 놓기·파일 선택·붙여넣기).

    recordings/<id>/manual_images/ 에 저장하고, 매뉴얼 마크다운에 쓸 상대 경로를 돌려준다.
    """
    d = rec_dir_of(rid)
    f = request.files.get("file")
    if not f:
        return jsonify(error="이미지 파일이 없습니다."), 400
    ext = Path(f.filename or "").suffix.lower() or {v: k for k, v in IMAGE_EXTS.items()}.get(f.mimetype, "")
    if ext not in IMAGE_EXTS:
        return jsonify(error="PNG, JPG, GIF, WEBP 이미지만 넣을 수 있습니다."), 400
    data = f.read()
    if len(data) > MAX_IMAGE_MB * 1024 * 1024:
        return jsonify(error=f"이미지는 {MAX_IMAGE_MB}MB 이하만 넣을 수 있습니다."), 400
    out = d / "manual_images"
    out.mkdir(exist_ok=True)
    name = f"{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000:03d}{'.jpg' if ext == '.jpeg' else ext}"
    (out / name).write_bytes(data)
    rel = f"../recordings/{rid}/manual_images/{name}"
    return jsonify(path=rel, url=rel.replace("../recordings/", "/files/recordings/"))


# ------------------------------------------------------------------ API: AI 기능 (수정 검토·묻고 답하기·말로 고치기)

def ai_ready() -> bool:
    return llm.is_configured()


@app.post("/api/manuals/<rid>/review")
def manual_review(rid: str):
    """고친 매뉴얼 검토: 코드 검사(항상) + AI 검사(AI를 켰을 때)."""
    from analyzer.review import review
    rec_dir_of(rid)
    body = request.json or {}
    return jsonify(review(rid, str(body.get("markdown", "")), use_ai=bool(body.get("use_ai")) and ai_ready()))


@app.get("/api/manuals/<rid>/chat")
def manual_chat(rid: str):
    """매뉴얼 묻고 답하기 기록."""
    from analyzer.assistant import load_chat
    rec_dir_of(rid)
    return jsonify(load_chat(rid))


@app.post("/api/manuals/<rid>/ask")
def manual_ask(rid: str):
    """매뉴얼에 대한 질문에 AI가 답한다."""
    from analyzer.assistant import ask
    rec_dir_of(rid)
    if not ai_ready():
        return jsonify(error="AI 설정이 없어 쓸 수 없습니다. .env에 AI 키(또는 사내 AI 주소)를 넣으세요."), 400
    q = str((request.json or {}).get("question", "")).strip()
    if not q:
        return jsonify(error="질문을 입력하세요."), 400
    try:
        return jsonify(ask(rid, q[:1000]))
    except Exception as e:
        return jsonify(error=f"AI 답변 실패: {str(e).splitlines()[0][:200]}"), 502


@app.post("/api/manuals/<rid>/chat/clear")
def manual_chat_clear(rid: str):
    from analyzer.assistant import clear_chat
    rec_dir_of(rid)
    clear_chat(rid)
    return jsonify(ok=True)


@app.post("/api/manuals/<rid>/criteria")
def manual_add_criteria(rid: str):
    """답변을 매뉴얼 '판단 기준'에 한 줄 추가 (이전 버전 보관)."""
    from analyzer.assistant import add_criteria
    rec_dir_of(rid)
    text = str((request.json or {}).get("text", "")).strip()
    if not text or not (MANUALS_DIR / f"{rid}.md").exists():
        return jsonify(error="추가할 내용이나 매뉴얼이 없습니다."), 400
    add_criteria(rid, text[:500])
    (RECORDINGS_DIR / rid / "manual_edited.json").write_text(json.dumps({"edited_at": time.strftime("%Y-%m-%dT%H:%M:%S")}), encoding="utf-8")
    return jsonify(manual_payload(rid))


@app.post("/api/edits/<rid>/<pid>/apply")
def edit_apply(rid: str, pid: str):
    """말로 고치기 변경안 적용 (매크로·매뉴얼 이전 버전 보관)."""
    from analyzer.edit import apply
    rec_dir_of(rid)
    if not re.fullmatch(r"\d{8}_\d{6}", pid):
        abort(404)
    try:
        prop = apply(rid, pid)
    except (ValueError, FileNotFoundError) as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True, summary=prop["summary"])


@app.post("/api/manuals/<rid>/undo")
def manual_undo(rid: str):
    """가장 최근에 보관한 이전 버전으로 되돌린다 (되돌린 버전은 보관 목록에서 뺀다)."""
    rec_dir_of(rid)
    d = manual_history_dir(rid)
    hist = sorted(d.glob("*.md")) if d.exists() else []
    if not hist:
        return jsonify(error="되돌릴 이전 버전이 없습니다."), 400
    last = hist[-1]
    (MANUALS_DIR / f"{rid}.md").write_text(last.read_text(encoding="utf-8"), encoding="utf-8")
    last.unlink()
    return jsonify(manual_payload(rid))


_pdf_lock = threading.Lock()   # PDF는 한 번에 하나씩 만든다 (브라우저를 띄우므로)

PDF_CSS = """
@page { size: A4; margin: 18mm 16mm 20mm; }
* { box-sizing: border-box; }
body { font-family: "Pretendard", "Malgun Gothic", "맑은 고딕", sans-serif; color: #1f2430; font-size: 10.5pt; line-height: 1.7; }
h1 { font-size: 20pt; margin: 0 0 10px; padding-bottom: 8px; border-bottom: 2px solid #4f46e5; }
h2 { font-size: 13.5pt; color: #4f46e5; margin: 22px 0 8px; page-break-after: avoid; }
blockquote { margin: 0 0 10px; padding: 6px 12px; background: #f4f5fb; border-left: 3px solid #4f46e5; color: #555; font-size: 9pt; }
code { background: #f1f2f8; padding: 1px 5px; border-radius: 4px; font-size: 9pt; word-break: break-all; }
ol > li, ul > li { margin-bottom: 6px; }
li { page-break-inside: avoid; }
img { display: block; max-width: 100%; max-height: 105mm; margin: 6px 0 12px; border: 1px solid #dfe3ee; border-radius: 6px; }
table { border-collapse: collapse; width: 100%; font-size: 9.5pt; }
th, td { border: 1px solid #dfe3ee; padding: 4px 8px; text-align: left; }
.cover-meta { color: #777; font-size: 9pt; margin-bottom: 16px; }
"""


def manual_pdf_html(rid: str, md: str) -> tuple[str, str]:
    """PDF용 HTML (스크린샷은 파일 안에 넣어 따로 불러올 필요 없게). (HTML, 제목)"""
    import base64
    import html as htmllib
    m = re.search(r"(?m)^# (.+)$", md)
    title = m.group(1).strip() if m else rid
    body = markdown.markdown(fix_josa(md), extensions=["tables", "sane_lists"], lazy_ol=False)

    def embed(mt: re.Match) -> str:
        p = (RECORDINGS_DIR / mt.group(1)).resolve()
        if RECORDINGS_DIR.resolve() not in p.parents or not p.exists():
            return 'src=""'
        mime = IMAGE_EXTS.get(p.suffix.lower(), "image/jpeg")
        return f'src="data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"'
    body = re.sub(r'src="\.\./recordings/([^"]+)"', embed, body)
    doc = (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>{htmllib.escape(title)}</title>"
           f"<style>{PDF_CSS}</style></head><body>{body}</body></html>")
    return doc, title


def render_pdf(doc: str) -> bytes:
    """Chromium(녹화에 쓰는 브라우저)으로 HTML을 PDF로 인쇄한다. 화면에 창은 뜨지 않는다."""
    from playwright.sync_api import sync_playwright
    footer = ('<div style="width:100%;font-size:8px;color:#999;text-align:center;font-family:Malgun Gothic,sans-serif">'
              '<span class="pageNumber"></span> / <span class="totalPages"></span></div>')
    with _pdf_lock, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(doc, wait_until="load")
            return page.pdf(format="A4", print_background=True, display_header_footer=True,
                            header_template="<span></span>", footer_template=footer,
                            margin={"top": "18mm", "bottom": "20mm", "left": "16mm", "right": "16mm"})
        finally:
            browser.close()


@app.get("/api/manuals/<rid>/pdf")
def manual_pdf(rid: str):
    """매뉴얼 PDF 다운로드 (스크린샷 포함, 쪽 번호)."""
    rec_dir_of(rid)
    p = MANUALS_DIR / f"{rid}.md"
    if not p.exists():
        abort(404)
    doc, title = manual_pdf_html(rid, p.read_text(encoding="utf-8"))
    try:
        pdf = render_pdf(doc)
    except Exception as e:
        return jsonify(error=f"PDF를 만들지 못했습니다: {str(e).splitlines()[0]}"), 500
    name = re.sub(r'[\\/:*?"<>|]+', "_", title).strip() or rid
    return send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
                     download_name=f"{name}_매뉴얼.pdf")


# ------------------------------------------------------------------ API: 매크로

@app.get("/api/macros")
def macros():
    """매크로 목록."""
    items = []
    for p in sorted(MACROS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        m = read_json(p)
        items.append({"id": p.stem, "name": m.get("name", ""), "steps": len(m["steps"]),
                      "variables": len(m["variables"]), "kind": m.get("kind", "web"),
                      "file_vars": [v["name"] for v in m["variables"] if v.get("type") == "file"],
                      "is_eval": p.stem.startswith("eval_")})
    return jsonify(items)


@app.get("/api/macros/<mid>")
def macro_detail(mid: str):
    """매크로 내용 + 오늘 기준 변수 기본값."""
    m = read_json(macro_path_of(mid))
    for s in m["steps"]:
        s["description"] = fix_josa(s.get("description", ""))
    for v in m["variables"]:
        v["description"] = fix_josa(v.get("description", ""))
        v["default"] = apply_rule(v["default_rule"], v["recorded_value"])
        v["secret"] = "비밀번호" in v["name"] or v["recorded_value"] == "***"
    return jsonify(m)


# ------------------------------------------------------------------ API: 업무 연결 (A 결과 → B 입력)

CHAINS_DIR = ROOT / "chains"


def chain_path_of(cid: str) -> Path:
    p = CHAINS_DIR / f"{cid}.json"
    if "/" in cid or "\\" in cid or ".." in cid or not p.exists():
        abort(404, "업무 연결을 찾을 수 없습니다")
    return p


def chain_view(c: dict) -> dict:
    """연결 목록용: 각 단계 매크로 이름·종류와 설정 문제."""
    from runner.chain import validate
    steps = []
    for s in c.get("steps", []):
        mp = MACROS_DIR / f"{s.get('macro', '')}.json"
        m = read_json(mp) if mp.exists() else {}
        steps.append({**s, "name": m.get("name") or s.get("macro"), "kind": m.get("kind", "web"), "exists": mp.exists()})
    return {**c, "steps": steps, "problems": validate(c)}


@app.get("/api/chains")
def chains():
    """저장된 업무 연결 목록."""
    if not CHAINS_DIR.exists():
        return jsonify([])
    items = [chain_view(read_json(p)) for p in sorted(CHAINS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)]
    return jsonify(items)


@app.post("/api/chains")
def chain_save():
    """업무 연결 저장. body: {name, steps: [{macro, link_var}]}"""
    from runner.chain import validate
    body = request.json or {}
    name = str(body.get("name", "")).strip()[:40]
    steps = [{"macro": str(s.get("macro", "")), **({"link_var": str(s["link_var"])} if i and s.get("link_var") else {})}
             for i, s in enumerate(body.get("steps") or [])]
    if not name:
        return jsonify(error="연결 이름을 입력하세요."), 400
    chain = {"id": re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_") + time.strftime("_%Y%m%d_%H%M%S"),
             "name": name, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "steps": steps}
    problems = validate(chain)
    if problems:
        return jsonify(error=" / ".join(problems)), 400
    CHAINS_DIR.mkdir(exist_ok=True)
    (CHAINS_DIR / f"{chain['id']}.json").write_text(json.dumps(chain, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify(chain_view(chain))


@app.post("/api/chains/<cid>/delete")
def chain_delete(cid: str):
    """업무 연결 삭제 (매크로는 지우지 않음)."""
    chain_path_of(cid).unlink()
    return jsonify(ok=True)


# ------------------------------------------------------------------ API: 작업

@app.post("/api/jobs")
def create_job():
    """작업 시작. kind: record / interpret / macro / questions / manual / run / chain / edit"""
    body = request.json or {}
    kind = body.get("kind")
    no_ai = [] if body.get("use_ai") else ["--no-ai"]

    if kind == "record":
        name = str(body.get("name", "")).strip()
        if not name:
            return jsonify(error="녹화 이름을 입력하세요."), 400
        if body.get("source") == "excel":
            if not excel_installed():
                return jsonify(error="이 PC에 Microsoft Excel이 없어 엑셀 녹화를 할 수 없습니다."), 400
            path = excel_file_of(str(body.get("file", "")))
            cmd = [PY, "-m", "recorder.excel_record", "--file", str(path), "--name", name]
        else:
            url = str(body.get("url", "")).strip() or MOCK_URL
            if url.startswith(MOCK_URL) and not mock_alive():
                start_mock()
            cmd = [PY, "-m", "recorder.record", "--url", url, "--name", name]
    elif kind in ("interpret", "macro", "questions", "manual"):
        rid = str(body.get("rid", ""))
        rec_dir_of(rid)
        module = {"interpret": "analyzer.interpret", "macro": "analyzer.variables",
                  "questions": "analyzer.questions", "manual": "analyzer.manual"}[kind]
        cmd = [PY, "-m", module, rid, *no_ai]
    elif kind == "run":
        path = macro_path_of(str(body.get("mid", "")))
        cmd = [PY, "-m", "runner.macro", str(path), "--yes"]
        for k, v in (body.get("values") or {}).items():
            cmd += ["--var", f"{k}={v}"]
        if body.get("allow_changes"):
            cmd.append("--allow-changes")
        if not body.get("checks", True):
            cmd.append("--no-checks")
        if body.get("headless"):
            cmd.append("--headless")
        if read_json(path).get("kind") == "excel":
            if not excel_installed():
                return jsonify(error="이 PC에 Microsoft Excel이 없어 엑셀 매크로를 실행할 수 없습니다."), 400
        elif uses_mock(path):
            start_mock()
    elif kind == "edit":
        rid = str(body.get("rid", ""))
        rec_dir_of(rid)
        req = str(body.get("request", "")).strip()
        if not req:
            return jsonify(error="무엇을 바꿀지 입력하세요."), 400
        if not ai_ready():
            return jsonify(error="말로 고치기는 AI가 필요합니다. .env에 AI 키(또는 사내 AI 주소)를 넣으세요."), 400
        RUNS_DIR.mkdir(exist_ok=True)
        stamp = int(time.time() * 1000)
        rfile, vfile = RUNS_DIR / f"_edit_req_{stamp}.json", RUNS_DIR / f"_edit_values_{stamp}.json"
        rfile.write_text(json.dumps({"request": req[:2000]}, ensure_ascii=False), encoding="utf-8")
        vfile.write_text(json.dumps(body.get("values") or {}, ensure_ascii=False), encoding="utf-8")
        cmd = [PY, "-m", "analyzer.edit", rid, "--request-file", str(rfile), "--values", str(vfile)]
        if uses_mock(MACROS_DIR / f"{rid}.json"):
            start_mock()
    elif kind == "chain":
        path = chain_path_of(str(body.get("cid", "")))
        chain = read_json(path)
        # 단계별 변수 값은 명령줄 대신 임시 파일로 넘긴다 (비밀번호가 프로세스 목록에 보이지 않게, 실행기가 읽고 지움)
        RUNS_DIR.mkdir(exist_ok=True)
        vfile = RUNS_DIR / f"_chain_values_{int(time.time() * 1000)}.json"
        vfile.write_text(json.dumps(body.get("values") or {}, ensure_ascii=False), encoding="utf-8")
        cmd = [PY, "-m", "runner.chain", str(path), "--values", str(vfile)]
        if body.get("allow_changes"):
            cmd.append("--allow-changes")
        if not body.get("checks", True):
            cmd.append("--no-checks")
        if body.get("headless"):
            cmd.append("--headless")
        kinds = {read_json(MACROS_DIR / f"{s['macro']}.json").get("kind", "web")
                 for s in chain["steps"] if (MACROS_DIR / f"{s['macro']}.json").exists()}
        if "excel" in kinds and not excel_installed():
            vfile.unlink(missing_ok=True)
            return jsonify(error="연결에 엑셀 매크로가 있는데 이 PC에 Microsoft Excel이 없습니다."), 400
        if any(uses_mock(MACROS_DIR / f"{s['macro']}.json") for s in chain["steps"]):
            start_mock()
    else:
        return jsonify(error=f"알 수 없는 작업: {kind}"), 400

    job = start_job(kind, cmd, {k: v for k, v in body.items() if k != "values"})
    return jsonify(id=job["id"])


@app.get("/api/jobs/<jid>")
def job_status(jid: str):
    """작업 진행 상태와 로그."""
    job = _jobs.get(jid)
    if not job:
        abort(404)
    return jsonify({k: job[k] for k in ("id", "kind", "status", "log", "result", "returncode")})


@app.errorhandler(400)
def bad_request(e):
    """API 400은 JSON으로 (화면에 이유를 보여 주기 위해)."""
    if request.path.startswith("/api/"):
        return jsonify(error=getattr(e, "description", "잘못된 요청")), 400
    return e


@app.errorhandler(404)
def not_found(e):
    """API 404는 JSON으로."""
    if request.path.startswith("/api/"):
        return jsonify(error=getattr(e, "description", "없음")), 404
    return e


def main() -> None:
    """가상 업무 시스템을 켜고, 관리 화면 서버를 띄운 뒤 브라우저를 연다."""
    import socket
    url = f"http://localhost:{PORT}"
    with socket.socket() as s:   # 이미 켜져 있으면 브라우저만 열고 끝낸다
        if s.connect_ex(("127.0.0.1", PORT)) == 0:
            print(f"관리 화면이 이미 실행 중입니다: {url}")
            webbrowser.open(url)
            return
    # 시험용 가상 시스템은 처음부터 켜지 않는다. 가상 사이트를 녹화·실행·열 때만 자동으로 켠다.
    print(f"관리 화면: {url}  (끄려면 이 창을 닫거나 Ctrl+C)")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
