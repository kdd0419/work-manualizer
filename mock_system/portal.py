"""가상 대학 포털 (새솔대학교 — 지어낸 학교). 주소: http://localhost:5000/portal

화면: 로그인, 홈, 전자출석, 강의 게시판(목록·글·댓글).
녹화·재실행을 위해 모든 입력·버튼에 의미 있는 id와 label을 단다.
"""
import re
from datetime import datetime
from functools import wraps

from flask import (Blueprint, abort, flash, redirect, render_template, request, session,
                   url_for)

from .db import get_conn

bp = Blueprint("portal", __name__, url_prefix="/portal")


def student_required(view):
    """포털 로그인을 하지 않았으면 포털 로그인 화면으로."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if "student" not in session:
            return redirect(url_for("portal.login"))
        return view(*args, **kwargs)
    return wrapper


def courses() -> list[dict]:
    """과목 목록."""
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM courses ORDER BY id")]


@bp.route("/login", methods=["GET", "POST"])
def login():
    """가짜 로그인: 학번만 입력하면 통과."""
    if request.method == "POST":
        sid = request.form.get("student_id", "").strip()
        if not sid:
            flash("학번을 입력하세요.", "error")
            return render_template("portal/login.html")
        session["student"] = sid
        return redirect(url_for("portal.home"))
    return render_template("portal/login.html")


@bp.route("/logout")
def logout():
    """포털 로그아웃."""
    session.pop("student", None)
    return redirect(url_for("portal.login"))


@bp.route("/")
@student_required
def home():
    """포털 홈: 바로가기, 수강 과목, 최근 게시글."""
    today = datetime.now().date().isoformat()
    with get_conn() as conn:
        recent = [dict(r) for r in conn.execute(
            "SELECT p.id, p.title, p.created_at, c.name AS course FROM posts p "
            "JOIN courses c ON c.id = p.course_id ORDER BY p.created_at DESC LIMIT 5")]
        done = {r["course_id"] for r in conn.execute(
            "SELECT course_id FROM attendance WHERE student_id=? AND att_date=?", (session["student"], today))}
    return render_template("portal/home.html", courses=courses(), recent=recent, done=done)


@bp.route("/attendance", methods=["GET", "POST"])
@student_required
def attendance():
    """전자출석: 과목 선택 + 출석 코드(4자리) → 출석. 같은 날 같은 과목은 한 번만."""
    sid = session["student"]
    today = datetime.now().date().isoformat()
    if request.method == "POST":
        course_id = request.form.get("course_id", "")
        code = request.form.get("attendance_code", "").strip()
        cmap = {str(c["id"]): c for c in courses()}
        if course_id not in cmap:
            flash("과목을 선택하세요.", "error")
        elif not re.fullmatch(r"\d{4}", code):
            flash("출석 코드는 숫자 4자리입니다.", "error")
        else:
            with get_conn() as conn:
                dup = conn.execute("SELECT 1 FROM attendance WHERE student_id=? AND course_id=? AND att_date=?",
                                   (sid, int(course_id), today)).fetchone()
                if dup:
                    flash(f"이미 오늘 출석한 과목입니다: {cmap[course_id]['name']}", "error")
                else:
                    now = datetime.now().strftime("%H:%M:%S")
                    conn.execute("INSERT INTO attendance (student_id, course_id, att_date, att_time) VALUES (?,?,?,?)",
                                 (sid, int(course_id), today, now))
                    flash(f"출석이 완료되었습니다. ({cmap[course_id]['name']}, {today} {now})", "success")
        return redirect(url_for("portal.attendance"))

    with get_conn() as conn:
        history = [dict(r) for r in conn.execute(
            "SELECT a.att_date, a.att_time, c.name FROM attendance a JOIN courses c ON c.id = a.course_id "
            "WHERE a.student_id=? ORDER BY a.id DESC LIMIT 10", (sid,))]
    return render_template("portal/attendance.html", courses=courses(), history=history, today=today)


@bp.route("/board")
@student_required
def board():
    """강의 게시판 목록 (과목별 조회)."""
    course = request.args.get("course", "전체")
    sql = ("SELECT p.id, p.title, p.author, p.created_at, p.views, c.name AS course, "
           "(SELECT COUNT(*) FROM comments m WHERE m.post_id = p.id) AS n_comments "
           "FROM posts p JOIN courses c ON c.id = p.course_id")
    params: list = []
    if course != "전체":
        sql += " WHERE c.name = ?"
        params.append(course)
    sql += " ORDER BY p.created_at DESC"
    with get_conn() as conn:
        posts = [dict(r) for r in conn.execute(sql, params)]
    return render_template("portal/board.html", posts=posts, courses=courses(), course=course)


@bp.route("/board/<int:post_id>")
@student_required
def post(post_id: int):
    """게시글 보기 + 댓글 목록 + 댓글 입력."""
    with get_conn() as conn:
        row = conn.execute("SELECT p.*, c.name AS course FROM posts p JOIN courses c ON c.id = p.course_id "
                           "WHERE p.id=?", (post_id,)).fetchone()
        if row is None:
            abort(404)
        conn.execute("UPDATE posts SET views = views + 1 WHERE id=?", (post_id,))
        comments = [dict(r) for r in conn.execute(
            "SELECT * FROM comments WHERE post_id=? ORDER BY id", (post_id,))]
    return render_template("portal/post.html", post=dict(row), comments=comments)


@bp.post("/board/<int:post_id>/comments")
@student_required
def add_comment(post_id: int):
    """댓글 등록."""
    content = request.form.get("content", "").strip()
    if not content:
        flash("댓글 내용을 입력하세요.", "error")
    else:
        with get_conn() as conn:
            if conn.execute("SELECT 1 FROM posts WHERE id=?", (post_id,)).fetchone() is None:
                abort(404)
            conn.execute("INSERT INTO comments (post_id, author, content, created_at) VALUES (?,?,?,?)",
                         (post_id, session["student"], content, datetime.now().isoformat(timespec="minutes")))
        flash("댓글이 등록되었습니다.", "success")
    return redirect(url_for("portal.post", post_id=post_id) + "#comments")
