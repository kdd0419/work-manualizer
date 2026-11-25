"""Excel 연동 공통 도구 (pywin32 COM). 엑셀 녹화기와 엑셀 매크로 실행기가 함께 쓴다.

- Excel 켜기/끄기 (사용자가 켜 둔 Excel과 섞이지 않게 별도 프로세스로 띄움)
- 시트 상태 읽기 (셀 수식·값, 필터 조건, 필터로 보이는 행)
- Excel 창 캡처 (다른 창에 가려져 있어도 캡처되는 PrintWindow 사용)
- 수식 범위 자동 확장 (녹화 파일과 실행 파일의 행 수가 다를 때)
"""
import ctypes
import re
from pathlib import Path

# Excel 상수
XL_VISIBLE = 12            # xlCellTypeVisible
XL_FORMULAS = -4123        # xlCellTypeFormulas
XL_ERRORS = 16             # xlErrors
XL_FILTER_VALUES = 7       # xlFilterValues
XL_AND, XL_OR = 1, 2
XL_A1, XL_R1C1 = 1, -4150
XLSX_FORMAT = 51           # xlOpenXMLWorkbook

# 사용자가 셀을 편집 중이거나 Excel이 바쁠 때 나는 오류 (잠시 뒤 다시 시도하면 됨)
BUSY_CODES = {-2147418111, -2147417846, -2146777998}


class ExcelBusy(Exception):
    """Excel이 바빠서 지금은 읽을 수 없음."""


def com_code(e: Exception) -> int | None:
    """COM 오류의 HRESULT 코드."""
    try:
        return e.args[0]
    except Exception:
        return None


def is_busy(e: Exception) -> bool:
    return com_code(e) in BUSY_CODES


def start_excel(visible: bool = True):
    """새 Excel 프로세스를 띄운다 (사용자가 열어 둔 Excel 창과 분리)."""
    import pythoncom
    import win32com.client as wc
    pythoncom.CoInitialize()
    xl = wc.DispatchEx("Excel.Application")
    xl.Visible = visible
    xl.DisplayAlerts = False
    return xl


def norm(x):
    """COM에서 읽은 값을 비교하기 쉬운 형태로 (1.0 → 1, None → '')."""
    if x is None:
        return ""
    if isinstance(x, float) and x.is_integer():
        return int(x)
    return x


def _grid(raw, nrows: int, ncols: int) -> list[list]:
    """Range.Formula/Value2 결과(스칼라 또는 튜플)를 2차원 리스트로."""
    if nrows == 1 and ncols == 1:
        return [[norm(raw)]]
    return [[norm(c) for c in row] for row in raw]


def col_letter(n: int) -> str:
    """1 → A, 27 → AA"""
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def last_data_row(grid: list[list]) -> int:
    """값이 있는 마지막 행 번호 (1부터). 빈 시트면 0."""
    for i in range(len(grid) - 1, -1, -1):
        if any(c != "" for c in grid[i]):
            return i + 1
    return 0


def last_data_col(grid: list[list]) -> int:
    """값이 있는 마지막 열 번호 (1부터)."""
    n = 0
    for row in grid:
        for j in range(len(row) - 1, -1, -1):
            if row[j] != "":
                n = max(n, j + 1)
                break
    return n


def read_filters(ws) -> dict[int, dict]:
    """자동 필터 조건 {열 번호: 조건}. 필터가 없으면 {}."""
    if not ws.AutoFilterMode:
        return {}
    af = ws.AutoFilter
    col0 = af.Range.Column
    out = {}
    for i in range(1, af.Filters.Count + 1):
        flt = af.Filters(i)
        if not flt.On:
            continue
        op = flt.Operator
        c1 = flt.Criteria1
        c2 = None
        if op in (XL_AND, XL_OR):
            try:
                c2 = flt.Criteria2
            except Exception:
                c2 = None
        out[col0 + i - 1] = criteria_to_cond(op, c1, c2)
    return out


def _strip_eq(v) -> str:
    v = str(v)
    return v[1:] if v.startswith("=") else v


def criteria_to_cond(op: int, c1, c2) -> dict:
    """Excel 필터 조건 → 사람이 읽기 쉬운 조건.

    값 목록(=A 또는 =B …)이면 {"type": "values", "values": [...]}, 그 밖(>=100 등)은 원래 조건 그대로.
    """
    items = list(c1) if isinstance(c1, (tuple, list)) else [c1]
    if op == XL_OR and c2 is not None:
        items.append(c2)
    is_values = op in (0, XL_OR, XL_FILTER_VALUES) and all(
        isinstance(v, str) and v.startswith("=") and not v.startswith("=*") for v in items)
    if is_values:
        return {"type": "values", "values": [_strip_eq(v) for v in items]}
    return {"type": "raw", "operator": op, "c1": c1 if not isinstance(c1, tuple) else list(c1), "c2": c2}


def visible_rows(ws, last_row: int) -> list[int] | None:
    """필터로 보이는 데이터 행 번호 목록. 필터가 아무것도 숨기지 않으면 None."""
    if last_row < 2 or not ws.FilterMode:
        return None
    try:
        rng = ws.Range(ws.Cells(2, 1), ws.Cells(last_row, 1)).SpecialCells(XL_VISIBLE)
    except Exception:
        return []   # 보이는 행이 하나도 없음
    rows = []
    for k in range(1, rng.Areas.Count + 1):
        a = rng.Areas(k)
        rows += list(range(a.Row, a.Row + a.Rows.Count))
    return rows


def read_sheet(ws) -> dict:
    """시트 하나의 상태: 수식(A1/R1C1), 값, 필터, 보이는 행."""
    ur = ws.UsedRange
    nrows = ur.Row + ur.Rows.Count - 1
    ncols = ur.Column + ur.Columns.Count - 1
    rng = ws.Range(ws.Cells(1, 1), ws.Cells(nrows, ncols))
    f = _grid(rng.Formula, nrows, ncols)
    last_r, last_c = last_data_row(f), last_data_col(f)
    f = [row[:last_c] for row in f[:last_r]]
    if last_r and last_c:
        rng = ws.Range(ws.Cells(1, 1), ws.Cells(last_r, last_c))
        r1c1 = _grid(rng.FormulaR1C1, last_r, last_c)
        v = _grid(rng.Value2, last_r, last_c)
    else:
        r1c1, v = [], []
    # 여러 번 나눠 읽는 사이에 사용자가 고치면 수식·값이 서로 다른 순간의 것이 됨 → 다시 읽어 확인
    if last_r and last_c:
        again = _grid(ws.Range(ws.Cells(1, 1), ws.Cells(last_r, last_c)).Formula, last_r, last_c)
        ur2 = ws.UsedRange
        if again != f or len(v) != last_r or ur2.Row + ur2.Rows.Count - 1 != nrows:
            raise ExcelBusy()
    return {"name": ws.Name, "f": f, "r1c1": r1c1, "v": v, "last_row": last_r, "last_col": last_c,
            "filters": read_filters(ws), "visible": visible_rows(ws, last_r)}


def snapshot(wb) -> dict:
    """통합 문서 전체 상태. Excel이 바쁘면 ExcelBusy."""
    try:
        sheets = [read_sheet(ws) for ws in wb.Worksheets]
        active = wb.ActiveSheet.Name
    except ExcelBusy:
        raise
    except Exception as e:
        if is_busy(e):
            raise ExcelBusy() from e
        raise
    return {"sheets": {s["name"]: s for s in sheets}, "order": [s["name"] for s in sheets], "active": active}


def headers(sheet: dict) -> list[str]:
    """1행(머리글) 글자 목록."""
    return [str(c) for c in sheet["f"][0]] if sheet["f"] else []


def sort_table(ws, col: int, desc: bool = False) -> None:
    """1행이 머리글인 표 전체를 한 열 기준으로 정렬한다 (Excel '데이터 → 정렬'과 같음)."""
    ur = ws.UsedRange
    last_r = ur.Row + ur.Rows.Count - 1
    last_c = ur.Column + ur.Columns.Count - 1
    rng = ws.Range(ws.Cells(1, 1), ws.Cells(last_r, last_c))
    s = ws.Sort
    s.SortFields.Clear()
    s.SortFields.Add(ws.Range(ws.Cells(2, col), ws.Cells(last_r, col)), 0, 2 if desc else 1)
    s.SetRange(rng)
    s.Header = 1   # xlYes: 1행은 머리글
    s.Apply()


# ------------------------------------------------------------------ 화면 캡처

_dpi_set = False


def capture_window(hwnd: int, path: Path, max_width: int = 1600, quality: int = 70) -> bool:
    """창 하나를 캡처해 JPEG로 저장한다 (다른 창에 가려져 있어도 됨)."""
    global _dpi_set
    import win32gui
    import win32ui
    from PIL import Image
    if not _dpi_set:   # 화면 배율(125%, 150%)에서도 창 크기를 정확히 읽도록
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
        _dpi_set = True
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0:
            return False
        hdc = win32gui.GetWindowDC(hwnd)
        mfc = win32ui.CreateDCFromHandle(hdc)
        sdc = mfc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc, w, h)
        sdc.SelectObject(bmp)
        ctypes.windll.user32.PrintWindow(hwnd, sdc.GetSafeHdc(), 2)   # 2 = PW_RENDERFULLCONTENT
        info = bmp.GetInfo()
        img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bmp.GetBitmapBits(True),
                               "raw", "BGRX", 0, 1)
        win32gui.DeleteObject(bmp.GetHandle())
        sdc.DeleteDC()
        mfc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hdc)
        if img.width > max_width:
            img = img.resize((max_width, round(img.height * max_width / img.width)))
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path, "JPEG", quality=quality)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ 수식 범위 자동 확장

_CELL = r"\$?[A-Z]{1,3}\$?\d+"
_REF = re.compile(
    r"(?P<sheet>(?:'[^']+'|[^\s'!:=(),+\-*/&<>^%\"{};]+)!)?"
    rf"(?<![A-Za-z0-9_.])(?P<a>{_CELL})(?::(?P<b>{_CELL}))?(?![\d(A-Za-z_])")
_CELL_PARTS = re.compile(r"(\$?[A-Z]{1,3})(\$?)(\d+)")


def adjust_formula(formula: str, cur_sheet: str, rec_last: dict[str, int], new_last: dict[str, int]) -> str:
    """수식 안의 셀 참조 중 '녹화 때 마지막 데이터 행 이후'를 실행 파일의 행 수에 맞게 옮긴다.

    예) 녹화 파일 37행, 실행 파일 45행:  =SUM(F2:F37) → =SUM(F2:F45),  거래내역!E2:E61 → 거래내역!E2:E73
    마지막 행보다 위(머리글, 2행 등)를 가리키는 참조는 그대로 둔다.
    """
    if not isinstance(formula, str) or not formula.startswith("="):
        return formula
    parts = formula.split('"')
    for i in range(0, len(parts), 2):   # 큰따옴표 안의 글자는 건드리지 않음
        def fix(m: re.Match) -> str:
            sheet = (m.group("sheet") or "")[:-1].strip("'") or cur_sheet
            L, N = rec_last.get(sheet), new_last.get(sheet)

            def cell(ref: str) -> str:
                c, dollar, r = _CELL_PARTS.fullmatch(ref).groups()
                r = int(r)
                if L and N and L >= 2 and r >= L:
                    r += N - L
                return f"{c}{dollar}{r}"
            out = f"{m.group('sheet') or ''}{cell(m.group('a'))}"
            return out + (f":{cell(m.group('b'))}" if m.group("b") else "")
        parts[i] = _REF.sub(fix, parts[i])
    return '"'.join(parts)


_R1C1_CELL = r"R(?:\[-?\d+\]|\d+)?C(?:\[-?\d+\]|\d+)?"
_R1C1_REF = re.compile(
    r"(?P<sheet>(?:'[^']+'|[^\s'!:=(),+\-*/&<>^%\"{};]+)!)?"
    rf"(?<![A-Za-z0-9_.])(?P<a>{_R1C1_CELL})(?::(?P<b>{_R1C1_CELL}))?(?![A-Za-z0-9_(])")
_R1C1_ROW = re.compile(r"^R(\d+)")


def adjust_r1c1(formula: str, cur_sheet: str, rec_last: dict[str, int], new_last: dict[str, int]) -> str:
    """R1C1 수식의 '절대 행 번호'(R37C6 같은)만 adjust_formula와 같은 규칙으로 옮긴다.

    상대 참조(RC[-5], R[-1]C)는 행마다 알아서 맞춰지므로 그대로 둔다. 열 채우기에 쓴다.
    """
    if not isinstance(formula, str) or not formula.startswith("="):
        return formula
    parts = formula.split('"')
    for i in range(0, len(parts), 2):
        def fix(m: re.Match) -> str:
            sheet = (m.group("sheet") or "")[:-1].strip("'") or cur_sheet
            L, N = rec_last.get(sheet), new_last.get(sheet)

            def cell(ref: str) -> str:
                rm = _R1C1_ROW.match(ref)
                if not rm or not (L and N and L >= 2):
                    return ref
                r = int(rm.group(1))
                return f"R{r + (N - L) if r >= L else r}{ref[rm.end():]}"
            out = f"{m.group('sheet') or ''}{cell(m.group('a'))}"
            return out + (f":{cell(m.group('b'))}" if m.group("b") else "")
        parts[i] = _R1C1_REF.sub(fix, parts[i])
    return '"'.join(parts)


def sheet_last_row(ws) -> int:
    """값이 있는 마지막 행 번호 (머리글만 있으면 1)."""
    ur = ws.UsedRange
    nrows = ur.Row + ur.Rows.Count - 1
    ncols = ur.Column + ur.Columns.Count - 1
    return last_data_row(_grid(ws.Range(ws.Cells(1, 1), ws.Cells(nrows, ncols)).Formula, nrows, ncols))


def sheet_last_col(ws) -> int:
    """1행(머리글)에서 값이 있는 마지막 열 번호."""
    ur = ws.UsedRange
    ncols = ur.Column + ur.Columns.Count - 1
    row = _grid(ws.Range(ws.Cells(1, 1), ws.Cells(1, ncols)).Formula, 1, ncols)
    return last_data_col(row)


def formula_errors(ws) -> str:
    """수식 오류(#DIV/0!, #N/A, #REF! …)가 있는 칸 주소. 없으면 ''."""
    try:
        rng = ws.UsedRange.SpecialCells(XL_FORMULAS, XL_ERRORS)
    except Exception:   # 오류 칸이 없으면 Excel이 예외를 냄
        return ""
    return address(rng)


def address(rng) -> str:
    """'$A$1:$B$2' → 'A1:B2' (pywin32에서는 Address에 인자를 줄 수 없어 $만 뺀다)."""
    return str(rng.Address).replace("$", "")


def cond_text(cond: dict) -> str:
    """필터·삭제 조건을 화면에 보여 줄 글자로."""
    if cond.get("type") == "values":
        return ", ".join(v if v != "" else "(빈 칸)" for v in cond["values"])
    c1, c2 = cond.get("c1"), cond.get("c2")
    joiner = {XL_AND: " 그리고 ", XL_OR: " 또는 "}.get(cond.get("operator"), " ")
    return f"{c1}{joiner + str(c2) if c2 else ''}"
