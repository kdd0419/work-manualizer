"""관리 화면 서버 (Flask). 브라우저에서 녹화·해석·매뉴얼·매크로 실행·평가를 모두 할 수 있다.

실행: python -m app.server   (또는 start.bat 더블클릭)
주소: http://localhost:8000

- 가상 업무 시스템(포트 5000)이 꺼져 있으면 자동으로 함께 켠다.
- 녹화·해석·매크로 실행 같은 오래 걸리는 작업은 별도 프로세스(작업)로 돌리고,
  화면은 /api/jobs/<id>를 주기적으로 조회해 진행 로그를 보여 준다.
"""
import atexit
import itertools
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import markdown
from flask import Flask, abort, jsonify, request, send_file, send_from_directory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analyzer.common import MACROS_DIR, MANUALS_DIR, RECORDINGS_DIR, read_json  # noqa: E402
from analyzer.llm_client import model_name  # noqa: E402  (.env도 여기서 읽힘)
from runner.dates import apply_rule  # noqa: E402

PORT = 8000
MOCK_URL = "http://localhost:5000"
RUNS_DIR = ROOT / "runs"
RESULTS_DIR = ROOT / "docs" / "results"
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
    """마크다운 → HTML. 매뉴얼의 스크린샷 상대 경로를 /files/ 주소로 바꾼다."""
    html = markdown.markdown(md, extensions=["tables", "sane_lists"])
    return html.replace('src="../recordings/', 'src="/files/recordings/')


def excel_installed() -> bool:
    """이 PC에 Microsoft Excel이 설치되어 있는지 (엑셀 녹화·실행에 필요)."""
    try:
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Excel.Application\CurVer"))
        return True
    except Exception:
        return False


def excel_file_of(rel: str) -> Path:
    """samples/ 또는 uploads/ 안의 엑셀 파일만 허용 (경로 조작 방지)."""
    p = (ROOT / rel).resolve()
    ok_dir = any(p.parent == d.resolve() for d in (SAMPLES_DIR, UPLOADS_DIR))
    if not ok_dir or p.suffix.lower() not in EXCEL_EXTS or not p.is_file():
        abort(400, "samples/ 또는 uploads/ 폴더의 엑셀 파일만 쓸 수 있습니다.")
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


# ------------------------------------------------------------------ API: 상태

@app.get("/api/status")
def status():
    """가상 업무 시스템·AI 키 상태."""
    return jsonify(mock=mock_alive(), mock_url=MOCK_URL, excel=excel_installed(),
                   has_key=bool(os.getenv("LLM_API_KEY", "").strip()), model=model_name())


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
    mp = MACROS_DIR / f"{rid}.json"
    out["macro_data"] = read_json(mp) if mp.exists() else None
    qs = read_json(d / "questions.json")["questions"] if (d / "questions.json").exists() else []
    ans = {a["id"]: a["answer"] for a in read_json(d / "answers.json")["answers"]} \
        if (d / "answers.json").exists() else {}
    out["questions"] = [{**q, "answer": ans.get(q["id"], "")} for q in qs]
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
    """매뉴얼 HTML 미리보기."""
    rec_dir_of(rid)
    p = MANUALS_DIR / f"{rid}.md"
    if not p.exists():
        return jsonify(exists=False)
    md = p.read_text(encoding="utf-8")
    return jsonify(exists=True, html=md_to_html(md), markdown=md)


@app.get("/api/manuals/<rid>/download")
def manual_download(rid: str):
    """매뉴얼 마크다운 파일 다운로드."""
    rec_dir_of(rid)
    p = MANUALS_DIR / f"{rid}.md"
    if not p.exists():
        abort(404)
    return send_file(p, as_attachment=True, download_name=p.name)


# ------------------------------------------------------------------ API: 매크로

@app.get("/api/macros")
def macros():
    """매크로 목록."""
    items = []
    for p in sorted(MACROS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        m = read_json(p)
        items.append({"id": p.stem, "name": m.get("name", ""), "steps": len(m["steps"]),
                      "variables": len(m["variables"]), "kind": m.get("kind", "web"),
                      "is_eval": p.stem.startswith("eval_")})
    return jsonify(items)


@app.get("/api/macros/<mid>")
def macro_detail(mid: str):
    """매크로 내용 + 오늘 기준 변수 기본값."""
    m = read_json(macro_path_of(mid))
    for v in m["variables"]:
        v["default"] = apply_rule(v["default_rule"], v["recorded_value"])
        v["secret"] = "비밀번호" in v["name"] or v["recorded_value"] == "***"
    return jsonify(m)


# ------------------------------------------------------------------ API: 작업

@app.post("/api/jobs")
def create_job():
    """작업 시작. kind: record / interpret / macro / questions / manual / run / eval"""
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
        elif not mock_alive():
            start_mock()
    elif kind == "eval":
        if not mock_alive():
            start_mock()
        cmd = [PY, "-m", "eval.run_all", *no_ai]
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


# ------------------------------------------------------------------ API: 평가 결과

@app.get("/api/results")
def results():
    """docs/results의 평가 보고서 목록 (최신 먼저, HTML로 변환)."""
    if not RESULTS_DIR.exists():
        return jsonify([])
    items = []
    for p in sorted(RESULTS_DIR.glob("*.md"), key=lambda p: p.name, reverse=True):
        if p.name == "README.md":
            continue
        md = p.read_text(encoding="utf-8")
        m = re.search(r"성공률: \*\*(\d+)/(\d+)", md)
        items.append({"name": p.stem, "html": md_to_html(md),
                      "ok": int(m.group(1)) if m else None, "total": int(m.group(2)) if m else None})
    return jsonify(items)


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
    start_mock()
    print(f"관리 화면: {url}  (끄려면 이 창을 닫거나 Ctrl+C)")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
