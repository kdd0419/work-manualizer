"""엑셀 녹화 자동 시연 스크립트 (사람 대신 Excel을 조작). 평가·테스트용.

각 시나리오는 Excel 조작 함수 목록이다. 녹화기가 함수 하나를 실행할 때마다 상태를 비교해 이벤트를 남긴다.
사람이 하는 조작(필터 메뉴, 채우기 핸들, 행 삭제 메뉴)과 같은 결과가 나도록 Excel 기능을 그대로 부른다.
"""
from recorder.excel_com import XL_FILTER_VALUES, XL_VISIBLE, sort_table


def _ws(wb, name=None):
    return wb.Worksheets(name) if name else wb.Worksheets(1)


def _last(ws) -> int:
    return ws.Cells(ws.Rows.Count, 1).End(-4162).Row   # xlUp


def filter_values(col: int, values: list[str]):
    def run(xl, wb):
        rng = _ws(wb).UsedRange
        if len(values) == 1:
            rng.AutoFilter(col, f"={values[0]}")
        else:
            rng.AutoFilter(col, values, XL_FILTER_VALUES)
    return run


def fill_visible(col_letter: str, value):
    """필터로 보이는 행의 한 열에 같은 값 (보이는 셀 선택 후 Ctrl+Enter와 같음)."""
    def run(xl, wb):
        ws = _ws(wb)
        ws.Range(f"{col_letter}2:{col_letter}{_last(ws)}").SpecialCells(XL_VISIBLE).Value = value
    return run


def clear_filter():
    def run(xl, wb):
        _ws(wb).AutoFilterMode = False
    return run


def sort_by(col: int, desc: bool = False):
    def run(xl, wb):
        sort_table(_ws(wb), col, desc)
    return run


def delete_where(col: int, values: list[str]):
    """조건에 맞는 행을 통째로 삭제 (필터 → 보이는 행 삭제와 같은 결과)."""
    def run(xl, wb):
        ws = _ws(wb)
        for r in range(_last(ws), 1, -1):
            if str(ws.Cells(r, col).Value) in values:
                ws.Rows(r).Delete()
    return run


def set_cells(cells: dict, sheet=None):
    """{'A40': 값 또는 수식}. 수식 안의 {last}는 현재 마지막 데이터 행."""
    def run(xl, wb):
        ws = _ws(wb, sheet)
        last = _last(_ws(wb))
        for addr, v in cells.items():
            addr = addr.format(last=last, next=last + 1)
            ws.Range(addr).Formula = v.format(last=last) if isinstance(v, str) else v
    return run


def add_sheet(name: str):
    def run(xl, wb):
        # 이름 옵션(After=)은 COM에서 무시되므로 위치 순서로 넘긴다 (Before 없음, After=마지막 시트)
        ws = wb.Worksheets.Add(None, wb.Worksheets(wb.Worksheets.Count))
        ws.Name = name
    return run


def fill_column_formula(col_letter: str, header: str, formula_first_row: str):
    """새 열 머리글을 쓰고, 2행에 수식을 넣은 뒤 마지막 행까지 채우기 (채우기 핸들 더블클릭과 같음)."""
    def run(xl, wb):
        ws = _ws(wb)
        last = _last(ws)
        ws.Range(f"{col_letter}1").Value = header
        ws.Range(f"{col_letter}2").Formula = formula_first_row
        ws.Range(f"{col_letter}2:{col_letter}{last}").FillDown()
    return run


# ------------------------------------------------------------------ 시연 시나리오

# 점검 파일: 부적합·보완 필요 행만 남기고 담당자 지정, 점검일자순 정렬, 건수 합계 행
INSPECTION_DEMO = [
    filter_values(6, ["부적합"]),
    fill_visible("G", "김민지"),
    clear_filter(),
    delete_where(6, ["적합"]),
    sort_by(2),
    set_cells({"A{next}": "합계", "B{next}": "=COUNTA(A2:A{last})"}),
]

# 거래 파일: 수수료 열 추가 + 업체별 금액 합계 시트
TRADE_DEMO = [
    fill_column_formula("G", "수수료", "=F2*0.001"),
    add_sheet("업체별합계"),
    set_cells({"A1": "업체명", "B1": "금액 합계", "C1": "건수 합계"}, sheet="업체별합계"),
    set_cells({"A2": "한빛IT서비스", "B2": "=SUMIF(거래내역!C2:C{last},A2,거래내역!F2:F{last})",
               "C2": "=SUMIF(거래내역!C2:C{last},A2,거래내역!E2:E{last})"}, sheet="업체별합계"),
    set_cells({"A3": "든든보안", "B3": "=SUMIF(거래내역!C2:C{last},A3,거래내역!F2:F{last})",
               "C3": "=SUMIF(거래내역!C2:C{last},A3,거래내역!E2:E{last})"}, sheet="업체별합계"),
]
