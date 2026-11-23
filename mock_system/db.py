"""SQLite 연결과 테이블 정의."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "mock.db"

# 업체 구분, 점검 결과, 상품 구분 선택지 (화면 드롭다운과 시드에서 함께 사용)
VENDOR_CATEGORIES = ["IT", "시설관리", "콜센터", "보안", "인쇄발송"]
INSPECTION_RESULTS = ["적합", "보완 필요", "부적합"]
STATUS_OPTIONS = INSPECTION_RESULTS + ["미점검"]
PRODUCTS = ["주식", "채권", "펀드", "ELS", "파생상품"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    manager TEXT NOT NULL,
    contract_start TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inspections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id INTEGER NOT NULL REFERENCES vendors(id),
    inspection_date TEXT NOT NULL,
    result TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    product TEXT NOT NULL,
    trade_count INTEGER NOT NULL,
    amount INTEGER NOT NULL
);
-- 가상 대학 포털 (새솔대학교, 지어낸 학교)
CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    professor TEXT NOT NULL,
    schedule TEXT NOT NULL,
    room TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    course_id INTEGER NOT NULL REFERENCES courses(id),
    att_date TEXT NOT NULL,
    att_time TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES courses(id),
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    views INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id),
    author TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


@contextmanager
def get_conn():
    """행을 dict처럼 읽을 수 있는 DB 연결. with 블록이 끝나면 커밋하고 닫는다."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    """테이블이 없으면 만든다."""
    conn.executescript(SCHEMA)
