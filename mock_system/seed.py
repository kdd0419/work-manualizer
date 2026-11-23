"""가상 데이터 생성: 업체 20개, 점검 이력, 거래 데이터 3개월치.

실행: python -m mock_system.seed [--base-date 2026-11-18]
모든 이름과 숫자는 지어낸 값이다. 같은 기준일이면 항상 같은 데이터가 만들어진다.
"""
import argparse
import random
from datetime import date, datetime, timedelta

from .db import (DB_PATH, INSPECTION_RESULTS, PRODUCTS, VENDOR_CATEGORIES,
                 get_conn, init_schema)

# 지어낸 업체명 (업체명, 업체 구분)
VENDORS = [
    ("샘플IT서비스", "IT"), ("가나다시스템", "IT"), ("한빛클라우드랩", "IT"),
    ("누리데이터웍스", "IT"), ("별빛소프트", "IT"), ("미르네트웍스", "IT"),
    ("다온시설관리", "시설관리"), ("푸른빌딩케어", "시설관리"), ("새솔환경", "시설관리"),
    ("하늘콜센터", "콜센터"), ("바른고객센터", "콜센터"), ("온누리컨택", "콜센터"),
    ("든든보안", "보안"), ("철벽시큐리티", "보안"), ("지킴이경비", "보안"),
    ("빠른인쇄", "인쇄발송"), ("우편나라", "인쇄발송"), ("모아프린팅", "인쇄발송"),
    ("라온IT솔루션", "IT"), ("소담시설", "시설관리"),
]
MANAGERS = ["김가상", "이샘플", "박예시", "최테스트", "정모의"]
NOTES = {
    "적합": ["특이사항 없음", "전 항목 기준 충족", ""],
    "보완 필요": ["접근권한 점검표 미제출", "보안교육 이수율 미달", "백업 점검 기록 누락"],
    "부적합": ["재위탁 사전 승인 누락", "개인정보 처리 기준 위반 정황"],
}


def seed(base_date: date) -> None:
    """DB를 새로 만들고 가상 데이터를 채운다."""
    if DB_PATH.exists():
        DB_PATH.unlink()
    rng = random.Random(20261118)
    with get_conn() as conn:
        _fill(conn, rng, base_date)
    print(f"가상 데이터 생성 완료: {DB_PATH} (기준일 {base_date})")


def _fill(conn, rng: random.Random, base_date: date) -> None:
    """테이블을 만들고 업체·점검·거래 데이터를 넣는다."""
    init_schema(conn)
    assert all(c in VENDOR_CATEGORIES for _, c in VENDORS)

    # 업체
    for i, (name, category) in enumerate(VENDORS, start=1):
        start = base_date - timedelta(days=rng.randint(200, 1500))
        conn.execute(
            "INSERT INTO vendors (id, name, category, manager, contract_start) VALUES (?,?,?,?,?)",
            (i, name, category, rng.choice(MANAGERS), start.isoformat()),
        )

    # 점검 이력: 업체별 0~3건, 최근 6개월 안 (일부 업체는 미점검으로 남김)
    now = datetime.now().isoformat(timespec="seconds")
    for vendor_id in range(1, len(VENDORS) + 1):
        for _ in range(rng.choice([0, 1, 1, 2, 3])):
            d = base_date - timedelta(days=rng.randint(1, 180))
            result = rng.choices(INSPECTION_RESULTS, weights=[6, 3, 1])[0]
            conn.execute(
                "INSERT INTO inspections (vendor_id, inspection_date, result, note, created_at)"
                " VALUES (?,?,?,?,?)",
                (vendor_id, d.isoformat(), result, rng.choice(NOTES[result]), now),
            )

    # 거래 데이터: 기준일 포함 최근 3개월(약 92일), 평일만, 상품별 하루 1행
    base_amount = {"주식": 50_000, "채권": 30_000, "펀드": 12_000, "ELS": 8_000, "파생상품": 20_000}
    d = base_date - timedelta(days=91)
    while d <= base_date:
        if d.weekday() < 5:
            for product in PRODUCTS:
                count = rng.randint(80, 600)
                amount = count * base_amount[product] * rng.randint(70, 130) * 10
                conn.execute(
                    "INSERT INTO trades (trade_date, product, trade_count, amount) VALUES (?,?,?,?)",
                    (d.isoformat(), product, count, amount),
                )
        d += timedelta(days=1)

    _fill_portal(conn, rng, base_date)


# 가상 대학 포털 데이터 (새솔대학교는 지어낸 학교, 교수·학생 이름도 모두 가상)
COURSES = [
    (1, "CSE2010", "데이터베이스설계", "김가상", "월·수 10:30", "공학관 301"),
    (2, "CSE3020", "웹프로그래밍", "이샘플", "화·목 13:00", "공학관 405"),
    (3, "AI2001", "인공지능개론", "박예시", "월 15:00", "IT관 210"),
    (4, "BIZ2100", "경영정보시스템", "최테스트", "수 09:00", "경영관 112"),
    (5, "GEN1001", "글쓰기와 소통", "정모의", "금 11:00", "인문관 204"),
]
POSTS = [
    (1, 1, "[공지] 중간고사 범위 안내", "김가상", "중간고사 범위는 1~7주차 강의 자료입니다. 정규화와 트랜잭션 부분을 꼭 복습하세요."),
    (2, 1, "ER 다이어그램 과제 질문 있습니다", "가상학생01", "관계의 카디널리티를 표시할 때 표기법은 자유인가요?"),
    (3, 2, "[과제] 3주차 실습 - 로그인 화면 만들기", "이샘플", "Flask로 로그인 화면을 만들고 화면 캡처를 제출하세요. 마감은 다음 주 화요일입니다."),
    (4, 2, "팀 프로젝트 팀원 구합니다", "가상학생07", "웹 서비스 주제로 함께할 팀원 2명 구합니다. 댓글 남겨 주세요."),
    (5, 3, "[공지] 다음 주 휴강 및 보강 안내", "박예시", "다음 주 월요일은 휴강이며, 보강은 온라인 강의로 대체합니다."),
    (6, 3, "퍼셉트론 실습 코드 공유", "가상학생12", "수업 시간에 다룬 퍼셉트론 예제를 정리해 봤습니다."),
    (7, 4, "[공지] 사례 발표 순서", "최테스트", "사례 발표 순서는 출석부 순서를 따릅니다."),
    (8, 5, "[과제] 자기소개 글쓰기", "정모의", "800자 내외로 자기소개 글을 작성해 댓글로 제출하세요."),
]
COMMENTS = [
    (2, "김가상", "표기법은 자유지만 범례를 꼭 함께 적어 주세요."),
    (4, "가상학생03", "저 참여하고 싶습니다!"),
    (6, "가상학생05", "정리 감사합니다. 도움이 많이 됐어요."),
    (8, "가상학생09", "안녕하세요, 저는 경영학과 가상학생09입니다…"),
]


def _fill_portal(conn, rng: random.Random, base_date: date) -> None:
    """과목 5개, 게시글 8개, 댓글 몇 개를 넣는다."""
    conn.executemany("INSERT INTO courses VALUES (?,?,?,?,?,?)", COURSES)
    for i, (pid, cid, title, author, content) in enumerate(POSTS):
        created = datetime.combine(base_date - timedelta(days=len(POSTS) - i), datetime.min.time()) \
            + timedelta(hours=9 + i)
        conn.execute("INSERT INTO posts VALUES (?,?,?,?,?,?,?)",
                     (pid, cid, title, author, content, created.isoformat(timespec="minutes"),
                      rng.randint(12, 180)))
    for pid, author, content in COMMENTS:
        conn.execute("INSERT INTO comments (post_id, author, content, created_at) VALUES (?,?,?,?)",
                     (pid, author, content, f"{base_date - timedelta(days=1)}T18:{rng.randint(10, 59)}"))


def main() -> None:
    """명령줄 인자를 읽어 시드를 실행한다."""
    parser = argparse.ArgumentParser(description="가상 업무 데이터 생성")
    parser.add_argument("--base-date", default=date.today().isoformat(),
                        help="데이터 기준일 (기본: 오늘)")
    args = parser.parse_args()
    seed(date.fromisoformat(args.base_date))


if __name__ == "__main__":
    main()
