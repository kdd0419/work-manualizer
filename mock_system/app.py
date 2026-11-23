"""가상 증권사 본부 업무 시스템.

실행: python -m mock_system.app  →  http://localhost:5000
"""
import csv
import io
from datetime import date, datetime
from functools import wraps

from flask import (Flask, flash, redirect, render_template, request, send_file,
                   session, url_for)

from .db import (DB_PATH, INSPECTION_RESULTS, PRODUCTS, STATUS_OPTIONS,
                 VENDOR_CATEGORIES, get_conn)

app = Flask(__name__)
app.secret_key = "mock-system-only-not-a-real-secret"  # 가상 시스템 전용 세션 키

from .portal import bp as portal_bp  # noqa: E402  가상 대학 포털 (/portal)

app.register_blueprint(portal_bp)


def login_required(view):
    """로그인하지 않았으면 로그인 화면으로 보낸다."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapper


def compact(d: str) -> str:
    """'2026-11-18' → '20261118' (다운로드 파일명용)."""
    return d.replace("-", "")


def csv_response(header: list[str], rows: list[list], filename: str):
    """엑셀에서 한글이 깨지지 않도록 UTF-8 BOM을 붙인 CSV를 내려준다."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    data = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
    return send_file(data, mimetype="text/csv", as_attachment=True, download_name=filename)


# ---------------------------------------------------------------- 로그인 / 메뉴

@app.route("/")
def index():
    """첫 화면: 녹화할 가상 시스템 선택 (증권사 업무 시스템 / 대학 포털)."""
    return render_template("landing.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    """가짜 로그인: 아이디만 입력하면 통과한다."""
    if request.method == "POST":
        user_id = request.form.get("user_id", "").strip()
        if not user_id:
            flash("아이디를 입력하세요.", "error")
            return render_template("login.html")
        session["user"] = user_id
        return redirect(url_for("menu"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    """세션을 지우고 로그인 화면으로 돌아간다."""
    session.clear()
    return redirect(url_for("login"))


@app.route("/menu")
@login_required
def menu():
    """메인 메뉴."""
    return render_template("menu.html")


# ---------------------------------------------------------------- 위탁업체 점검 현황

def query_inspection_status(inquiry_date: str, category: str, status: str) -> list[dict]:
    """기준일자 시점의 업체별 최근 점검 결과를 조회한다."""
    sql = """
    SELECT v.id, v.name, v.category, v.manager,
           (SELECT i.inspection_date FROM inspections i
             WHERE i.vendor_id = v.id AND i.inspection_date <= :d
             ORDER BY i.inspection_date DESC, i.id DESC LIMIT 1) AS last_date,
           (SELECT i.result FROM inspections i
             WHERE i.vendor_id = v.id AND i.inspection_date <= :d
             ORDER BY i.inspection_date DESC, i.id DESC LIMIT 1) AS last_result,
           (SELECT i.note FROM inspections i
             WHERE i.vendor_id = v.id AND i.inspection_date <= :d
             ORDER BY i.inspection_date DESC, i.id DESC LIMIT 1) AS last_note
      FROM vendors v
     WHERE v.contract_start <= :d
     ORDER BY v.id
    """
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(sql, {"d": inquiry_date})]
    for r in rows:
        r["status"] = r["last_result"] or "미점검"
    if category != "전체":
        rows = [r for r in rows if r["category"] == category]
    if status != "전체":
        rows = [r for r in rows if r["status"] == status]
    return rows


def inspection_params() -> tuple[str, str, str]:
    """조회 조건을 요청에서 읽는다 (없으면 기본값)."""
    return (
        request.args.get("inquiry_date") or date.today().isoformat(),
        request.args.get("vendor_category", "전체"),
        request.args.get("vendor_status", "전체"),
    )


@app.route("/inspections/status")
@login_required
def inspection_status():
    """위탁업체 점검 현황 조회 화면."""
    inquiry_date, category, status = inspection_params()
    searched = "inquiry_date" in request.args
    rows = query_inspection_status(inquiry_date, category, status) if searched else []
    return render_template(
        "inspection_status.html", rows=rows, searched=searched,
        inquiry_date=inquiry_date, category=category, status=status,
        categories=VENDOR_CATEGORIES, statuses=STATUS_OPTIONS,
    )


@app.route("/inspections/status/download")
@login_required
def inspection_status_download():
    """조회 조건 그대로 CSV 다운로드. 파일명: 위탁업체점검_YYYYMMDD.csv"""
    inquiry_date, category, status = inspection_params()
    rows = query_inspection_status(inquiry_date, category, status)
    return csv_response(
        ["업체코드", "업체명", "업체구분", "담당자", "최근점검일", "점검상태", "비고"],
        [[r["id"], r["name"], r["category"], r["manager"], r["last_date"] or "",
          r["status"], r["last_note"] or ""] for r in rows],
        f"위탁업체점검_{compact(inquiry_date)}.csv",
    )


# ---------------------------------------------------------------- 일별 거래 집계

def query_daily_trades(start: str, end: str, product: str) -> list[dict]:
    """기간·상품 조건으로 일별 거래 건수/금액을 조회한다."""
    sql = "SELECT trade_date, product, trade_count, amount FROM trades WHERE trade_date BETWEEN ? AND ?"
    params: list = [start, end]
    if product != "전체":
        sql += " AND product = ?"
        params.append(product)
    sql += " ORDER BY trade_date, product"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params)]


def trade_params() -> tuple[str, str, str]:
    """거래 조회 조건을 요청에서 읽는다 (기본: 이번 달 1일 ~ 오늘)."""
    today = date.today()
    return (
        request.args.get("start_date") or today.replace(day=1).isoformat(),
        request.args.get("end_date") or today.isoformat(),
        request.args.get("product_type", "전체"),
    )


@app.route("/trades/daily")
@login_required
def daily_trades():
    """일별 거래 집계 조회 화면."""
    start, end, product = trade_params()
    searched = "start_date" in request.args
    error = None
    rows: list[dict] = []
    if searched:
        if start > end:
            error = "시작일이 종료일보다 늦습니다."
        else:
            rows = query_daily_trades(start, end, product)
    totals = {
        "count": sum(r["trade_count"] for r in rows),
        "amount": sum(r["amount"] for r in rows),
    }
    return render_template(
        "daily_trades.html", rows=rows, searched=searched, error=error, totals=totals,
        start_date=start, end_date=end, product=product, products=PRODUCTS,
    )


@app.route("/trades/daily/download")
@login_required
def daily_trades_download():
    """CSV 다운로드. 파일명: 일별거래집계_시작일_종료일.csv"""
    start, end, product = trade_params()
    rows = query_daily_trades(start, end, product) if start <= end else []
    return csv_response(
        ["거래일자", "상품구분", "거래건수", "거래금액(원)"],
        [[r["trade_date"], r["product"], r["trade_count"], r["amount"]] for r in rows],
        f"일별거래집계_{compact(start)}_{compact(end)}.csv",
    )


# ---------------------------------------------------------------- 점검 결과 등록

@app.route("/inspections/new", methods=["GET", "POST"])
@login_required
def inspection_new():
    """점검 결과 등록 화면. 저장 후 완료 메시지를 보여준다."""
    with get_conn() as conn:
        vendors = [dict(r) for r in conn.execute("SELECT id, name, category FROM vendors ORDER BY id")]

    if request.method == "POST":
        vendor_id = request.form.get("vendor_id", "")
        inspection_date = request.form.get("inspection_date", "")
        result = request.form.get("inspection_result", "")
        note = request.form.get("note", "").strip()

        errors = []
        if not vendor_id.isdigit() or int(vendor_id) not in {v["id"] for v in vendors}:
            errors.append("업체를 선택하세요.")
        if not inspection_date:
            errors.append("점검일을 입력하세요.")
        if result not in INSPECTION_RESULTS:
            errors.append("점검 결과를 선택하세요.")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("inspection_new.html", vendors=vendors,
                                   results=INSPECTION_RESULTS, form=request.form), 400

        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO inspections (vendor_id, inspection_date, result, note, created_at)"
                " VALUES (?,?,?,?,?)",
                (int(vendor_id), inspection_date, result, note,
                 datetime.now().isoformat(timespec="seconds")),
            )
            new_id = cur.lastrowid
        name = next(v["name"] for v in vendors if v["id"] == int(vendor_id))
        flash(f"저장되었습니다. (등록번호 {new_id}, {name}, {inspection_date}, {result})", "success")
        return redirect(url_for("inspection_new"))

    return render_template("inspection_new.html", vendors=vendors,
                           results=INSPECTION_RESULTS,
                           form={"inspection_date": date.today().isoformat()})


def main() -> None:
    """DB가 없으면 먼저 시드를 만들고 서버를 띄운다.

    --no-debug: 관리 화면이 이 서버를 자식 프로세스로 띄울 때 사용 (자동 재시작 끔)
    """
    import sys
    if not DB_PATH.exists():
        from .seed import seed
        seed(date.today())
    debug = "--no-debug" not in sys.argv
    app.run(host="127.0.0.1", port=5000, debug=debug, use_reloader=debug)


if __name__ == "__main__":
    main()
