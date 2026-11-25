"""엑셀 녹화 시연용 가상 엑셀 파일을 만든다 (모든 업체·사람·금액은 지어낸 것).

실행: python -m samples.make_samples
결과: samples/위탁업체점검_2026-10.xlsx, 위탁업체점검_2026-11.xlsx,
      samples/거래내역_2026-10.xlsx, 거래내역_2026-11.xlsx

같은 형식의 파일을 달마다 하나씩 두어, 10월 파일로 녹화하고 11월 파일로 매크로를 다시 실행해 볼 수 있다.
행 수가 달라도 매크로가 동작하는지 확인하려고 두 달의 행 수를 다르게 했다.
"""
import random
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HERE = Path(__file__).resolve().parent

VENDORS = [("한빛IT서비스", "IT"), ("누리시설관리", "시설관리"), ("가온콜센터", "콜센터"),
           ("든든보안", "보안"), ("바른인쇄발송", "인쇄발송"), ("새봄IT솔루션", "IT"),
           ("푸른빌딩관리", "시설관리"), ("다온고객센터", "콜센터")]
ITEMS = ["개인정보 보호", "접근 권한 관리", "재해 복구 훈련", "계약 이행", "보안 교육", "출입 기록"]
RESULTS = ["적합"] * 6 + ["보완 필요"] * 2 + ["부적합"] * 2
PRODUCTS = ["주식", "채권", "펀드", "ELS", "파생상품"]

HEAD_FILL = PatternFill("solid", fgColor="1F4E78")
HEAD_FONT = Font(bold=True, color="FFFFFF")


def _write(path: Path, sheet: str, header: list[str], rows: list[list], widths: list[int]) -> None:
    """머리글 서식이 있는 시트 하나짜리 엑셀 파일을 저장한다."""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(header)
    for r in rows:
        ws.append(r)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
        c = ws.cell(row=1, column=i)
        c.fill, c.font, c.alignment = HEAD_FILL, HEAD_FONT, Alignment(horizontal="center")
    ws.freeze_panes = "A2"
    wb.save(path)


def inspections(month: int, count: int, rnd: random.Random) -> None:
    """위탁업체 점검 현황 (점검번호가 행을 구분하는 열쇠)."""
    rows = []
    for i in range(count):
        name, cat = rnd.choice(VENDORS)
        day = rnd.randint(1, 28)
        result = rnd.choice(RESULTS)
        note = {"부적합": "재점검 필요", "보완 필요": "보완 계획 제출 요청"}.get(result, "")
        rows.append([f"INS-{month:02d}{i + 1:03d}", f"2026-{month:02d}-{day:02d}", name, cat,
                     rnd.choice(ITEMS), result, "", note])
    rows.sort(key=lambda r: r[0])
    _write(HERE / f"위탁업체점검_2026-{month:02d}.xlsx", "점검현황",
           ["점검번호", "점검일자", "업체명", "업체구분", "점검항목", "점검결과", "담당자", "비고"],
           rows, [12, 12, 16, 10, 16, 10, 10, 20])


def trades(month: int, count: int, rnd: random.Random) -> None:
    """일별 거래 내역 (업체별·상품별 합계를 내는 연습용)."""
    rows = []
    for i in range(count):
        name, _ = rnd.choice(VENDORS)
        n = rnd.randint(1, 40)
        rows.append([f"TR-{month:02d}{i + 1:03d}", f"2026-{month:02d}-{rnd.randint(1, 28):02d}",
                     name, rnd.choice(PRODUCTS), n, n * rnd.randint(8, 60) * 10000])
    rows.sort(key=lambda r: (r[1], r[0]))
    _write(HERE / f"거래내역_2026-{month:02d}.xlsx", "거래내역",
           ["거래번호", "거래일자", "업체명", "상품", "건수", "금액"], rows, [12, 12, 16, 10, 8, 14])


def main() -> None:
    """두 달 치 샘플 파일 4개를 만든다 (항상 같은 내용이 나오도록 난수 고정)."""
    rnd = random.Random(20261118)
    inspections(10, 36, rnd)
    inspections(11, 44, rnd)
    trades(10, 60, rnd)
    trades(11, 72, rnd)
    for p in sorted(HERE.glob("*.xlsx")):
        print(f"  {p.name}")


if __name__ == "__main__":
    main()
