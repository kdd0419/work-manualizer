"""엑셀 매크로 실행 (실제 Microsoft Excel 조작). runner.macro가 매크로 종류(kind=excel)를 보고 부른다.

- 입력 파일을 runs/<id>_<시각>/ 으로 복사해 그 복사본을 Excel로 연다 (원본은 절대 바꾸지 않음)
- 단계를 순서대로 실행하고 <원본이름>_결과.xlsx 로 저장한다
- v4 실패 감지: 파일 없음, 시트·열 없음, 열쇠값 행 없음, 필터/삭제 뒤 0행, 수식 오류(#DIV/0! 등)
  멈추면 그때까지의 작업을 <원본이름>_중단.xlsx 로 저장해 사람이 이어서 처리할 수 있게 한다.
"""
import shutil
import time
from pathlib import Path
from typing import Callable

from recorder.excel_com import (XL_FILTER_VALUES, XLSX_FORMAT, address, adjust_formula, adjust_r1c1,
                                capture_window, formula_errors, norm, sheet_last_col,
                                sheet_last_row, sort_table, start_excel, visible_rows)

from .common import (HUMAN_HANDOFF, ROOT, STOP_REASONS, finish_result, new_result, new_run_dir,
                     save_result)

STEP_DELAY = 0.5   # 창을 보여 줄 때 단계 사이 간격 (사람이 따라 볼 수 있게)


class StopRun(Exception):
    """실행을 멈춰야 하는 상황. code는 STOP_REASONS의 키."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code, self.detail = code, detail


def resolve_file(value: str) -> Path | None:
    """파일 경로 찾기: 그대로 → 프로젝트 기준 → samples/·uploads/ 에서 같은 이름."""
    p = Path(value)
    for cand in (p, ROOT / p, ROOT / "samples" / p.name, ROOT / "uploads" / p.name):
        if cand.is_file():
            return cand.resolve()
    return None


def _values(text: str) -> list[str]:
    """'보완 필요, 부적합' → ['보완 필요', '부적합'] ('(빈 칸)'은 빈 값)."""
    return ["" if v.strip() == "(빈 칸)" else v.strip() for v in str(text).split(",")]


def _groups(rows: list[int]) -> list[tuple[int, int]]:
    """[2,3,4,7,8] → [(2,4),(7,8)]"""
    out: list[tuple[int, int]] = []
    for r in sorted(rows):
        if out and r == out[-1][1] + 1:
            out[-1] = (out[-1][0], r)
        else:
            out.append((r, r))
    return out


class ExcelRunner:
    """열린 통합 문서에서 단계를 실행한다."""

    def __init__(self, xl, wb, first_sheet: str, checks: bool):
        self.xl, self.wb, self.checks = xl, wb, checks
        self.first_sheet = first_sheet
        self.sheet_map: dict[str, str] = {}
        self.note = ""

    # ---------------- 찾기
    def sheet(self, name: str):
        """녹화 때 시트 이름으로 시트를 찾는다. 첫 시트 이름만 다르면 첫 시트를 쓴다."""
        name = self.sheet_map.get(name, name)
        try:
            return self.wb.Worksheets(name)
        except Exception:
            pass
        if name == self.first_sheet:
            ws = self.wb.Worksheets(1)
            self.sheet_map[name] = ws.Name
            self.note += f"(시트 '{name}' 대신 첫 시트 '{ws.Name}' 사용) "
            return ws
        raise StopRun("excel_not_found", f"시트 '{name}'")

    def col(self, ws, header: str) -> int:
        """1행에서 머리글 이름으로 열 번호를 찾는다."""
        last = sheet_last_col(ws)
        if last:
            row = ws.Range(ws.Cells(1, 1), ws.Cells(1, last)).Value2
            row = row[0] if isinstance(row, tuple) else (row,)
            for j, v in enumerate(row, 1):
                if str(norm(v)).strip() == str(header).strip():
                    return j
        raise StopRun("excel_not_found", f"'{ws.Name}' 시트의 '{header}' 열")

    def column_values(self, ws, col: int, last: int) -> list:
        """2행부터 last행까지 한 열의 값."""
        if last < 2:
            return []
        raw = ws.Range(ws.Cells(2, col), ws.Cells(last, col)).Value2
        if not isinstance(raw, tuple):
            return [norm(raw)]
        return [norm(r[0]) for r in raw]

    def last_rows(self, names) -> dict[str, int]:
        """녹화 때 시트 이름 → 지금 파일의 마지막 데이터 행."""
        out = {}
        for n in names:
            try:
                out[n] = sheet_last_row(self.sheet(n))
            except StopRun:
                pass
        return out

    def check_errors(self, ws) -> None:
        if self.checks:
            bad = formula_errors(ws)
            if bad:
                raise StopRun("formula_error", f"오류 칸: {bad}")

    def check_structure(self, recorded: dict) -> str:
        """v4: 녹화 때 시트의 머리글이 같은 위치에 그대로 있는지 확인한다.

        수식은 열 위치(F열 등)로 계산하므로, 머리글이 빠지거나 순서가 바뀐 파일에서는 엉뚱한 값이 나온다.
        뒤에 열이 더 붙은 것은 괜찮다.
        """
        problems = []
        for name, info in recorded.items():
            want = [str(h) for h in info.get("headers", [])]
            if not want:
                continue
            try:
                ws = self.sheet(name)
            except StopRun:
                problems.append(f"시트 '{name}' 없음")
                continue
            last = sheet_last_col(ws)
            row = ws.Range(ws.Cells(1, 1), ws.Cells(1, max(last, 1))).Value2
            row = row[0] if isinstance(row, tuple) else (row,)
            have = [str(norm(v)).strip() for v in row]
            diff = [f"{i + 1}열 '{h}'" + (f"(지금 '{have[i]}')" if i < len(have) and have[i] else "(없음)")
                    for i, h in enumerate(want) if i >= len(have) or have[i] != h.strip()]
            if diff:
                problems.append(f"'{ws.Name}' 시트 머리글이 녹화 때와 다름: {', '.join(diff[:4])}"
                                + (" …" if len(diff) > 4 else ""))
        if problems:
            raise StopRun("structure_mismatch", " / ".join(problems))
        return "머리글 확인"

    # ---------------- 단계
    def run(self, step: dict, value: str) -> str:
        """단계 하나 실행. 화면에 보여 줄 메시지를 돌려준다."""
        ex = step.get("excel", {})
        op = ex.get("op")
        self.note = ""
        msg = getattr(self, f"op_{op}")(ex, value)
        return self.note + (msg or "")

    def op_filter(self, ex, value):
        ws = self.sheet(ex["sheet"])
        ws.Activate()
        c = self.col(ws, ex["header"])
        last = sheet_last_row(ws)
        rng = ws.Range(ws.Cells(1, 1), ws.Cells(max(last, 2), sheet_last_col(ws)))
        cond = ex["cond"]
        if cond.get("type") == "values":
            vals = _values(value)
            if len(vals) == 1:
                rng.AutoFilter(c, f"={vals[0]}")
            else:
                rng.AutoFilter(c, vals, XL_FILTER_VALUES)
        else:
            if cond.get("c2") is not None:
                rng.AutoFilter(c, cond["c1"], cond["operator"], cond["c2"])
            else:
                rng.AutoFilter(c, cond["c1"])
        vis = visible_rows(ws, last)
        n = (last - 1) if vis is None else len(vis)
        if self.checks and n == 0:
            raise StopRun("no_result", f"'{ex['header']}' 필터 결과 0행")
        return f"{last - 1}행 중 {n}행 표시"

    def op_unfilter(self, ex, value):
        ws = self.sheet(ex["sheet"])
        if not ws.AutoFilterMode:
            return "필터 없음"
        c = self.col(ws, ex["header"])
        ws.AutoFilter.Range.AutoFilter(c)   # 이 열의 조건만 해제
        if not any(ws.AutoFilter.Filters(i).On for i in range(1, ws.AutoFilter.Filters.Count + 1)):
            ws.AutoFilterMode = False       # 남은 조건이 없으면 필터 단추도 없앰
        return "필터 해제"

    def op_sort(self, ex, value):
        if not ex.get("header"):
            raise StopRun("excel_not_found", "정렬 기준 열 (녹화에서 알아내지 못함)")
        ws = self.sheet(ex["sheet"])
        ws.Activate()
        sort_table(ws, self.col(ws, ex["header"]), value == "내림차순" if value else ex.get("desc", False))
        return ""

    def _delete_rows(self, ws, rows: list[int]) -> None:
        for a, b in reversed(_groups(rows)):   # 아래에서부터 지워야 행 번호가 안 밀림
            ws.Range(ws.Rows(a), ws.Rows(b)).Delete()

    def op_delete_where(self, ex, value):
        ws = self.sheet(ex["sheet"])
        ws.Activate()
        c = self.col(ws, ex["header"])
        last = sheet_last_row(ws)
        targets = set(_values(value))
        rows = [i + 2 for i, v in enumerate(self.column_values(ws, c, last)) if str(v) in targets]
        self._delete_rows(ws, rows)
        left = sheet_last_row(ws) - 1
        if self.checks and left <= 0:
            raise StopRun("no_result", "행을 지우고 나니 남은 데이터가 0행")
        return f"{len(rows)}행 삭제, {left}행 남음"

    def op_delete_keys(self, ex, value):
        ws = self.sheet(ex["sheet"])
        if not ex.get("key_header"):
            raise StopRun("excel_not_found", "삭제할 행을 찾을 열쇠 열이 없음 (녹화 파일 전용 단계)")
        c = self.col(ws, ex["key_header"])
        keys = {str(k) for k in ex["keys"]}
        vals = self.column_values(ws, c, sheet_last_row(ws))
        rows = [i + 2 for i, v in enumerate(vals) if str(v) in keys]
        if self.checks and not rows:
            raise StopRun("excel_not_found", f"녹화 때 지운 행({ex['key_header']} {len(keys)}개)이 이 파일에 하나도 없음")
        self._delete_rows(ws, rows)
        return f"{len(rows)}/{len(keys)}행 삭제 (없는 행은 건너뜀)"

    def op_add_column(self, ex, value):
        ws = self.sheet(ex["sheet"])
        try:
            self.col(ws, ex["header"])
            return "이미 있는 열 (건너뜀)"
        except StopRun:
            pass
        c = sheet_last_col(ws) + 1
        ws.Cells(1, c).Value = ex["header"]
        return f"{c}번째 열"

    def op_rename_header(self, ex, value):
        ws = self.sheet(ex["sheet"])
        try:
            c = self.col(ws, ex["old"])
        except StopRun:
            self.col(ws, ex["header"])   # 이미 바뀐 이름이면 통과, 아니면 멈춤
            return "이미 바뀐 이름"
        ws.Cells(1, c).Value = value or ex["header"]
        return ""

    def op_fill(self, ex, value):
        ws = self.sheet(ex["sheet"])
        ws.Activate()
        c = self.col(ws, ex["header"])
        last = sheet_last_row(ws)
        scope = ex["scope"]
        if scope["type"] == "all":
            rows = list(range(2, last + 1))
        elif scope["type"] == "visible":
            vis = visible_rows(ws, last)
            rows = list(range(2, last + 1)) if vis is None else vis
        else:
            kc = self.col(ws, scope["header"])
            targets = {str(v) for v in scope["values"]}
            rows = [i + 2 for i, v in enumerate(self.column_values(ws, kc, last)) if str(v) in targets]
        if not rows:
            if self.checks:
                raise StopRun("no_result", f"'{ex['header']}' 열을 채울 행이 0행")
            return "채울 행 없음"
        if ex.get("r1c1"):
            names = ex.get("last_rows", {}).keys()
            f = adjust_r1c1(ex["r1c1"], ex["sheet"], ex.get("last_rows", {}), self.last_rows(names))
            for a, b in _groups(rows):
                ws.Range(ws.Cells(a, c), ws.Cells(b, c)).FormulaR1C1 = f
        else:
            for a, b in _groups(rows):
                ws.Range(ws.Cells(a, c), ws.Cells(b, c)).Value = value
        self.check_errors(ws)
        return f"{len(rows)}행"

    def op_set(self, ex, value):
        ws = self.sheet(ex["sheet"])
        c = self.col(ws, ex["header"])
        if ex.get("key_header") and ex.get("key") not in (None, ""):
            kc = self.col(ws, ex["key_header"])
            vals = self.column_values(ws, kc, sheet_last_row(ws))
            hits = [i + 2 for i, v in enumerate(vals) if str(v) == str(ex["key"])]
            if not hits:
                raise StopRun("excel_not_found", f"{ex['key_header']} '{ex['key']}' 행")
            row = hits[0]
        else:
            row = ex["rec_row"]
            self.note += "(열쇠 열이 없어 녹화 때 행 번호 사용) "
        cell = ws.Cells(row, c)
        if ex.get("r1c1"):
            names = ex.get("last_rows", {}).keys()
            cell.FormulaR1C1 = adjust_r1c1(ex["r1c1"], ex["sheet"], ex.get("last_rows", {}), self.last_rows(names))
        else:
            cell.Value = value
        self.check_errors(ws)
        return f"{address(cell)} 칸"

    def op_below(self, ex, value):
        ws = self.sheet(ex["sheet"])
        ws.Activate()
        rec_last = ex.get("last_rows", {})
        now_last = self.last_rows(rec_last.keys())
        base = sheet_last_row(ws)
        for cell in ex["cells"]:
            try:
                c = self.col(ws, cell["header"]) if cell.get("header") else cell["col"]
            except StopRun:
                c = cell["col"]
            ws.Cells(base + cell["offset"], c).Formula = adjust_formula(cell["formula"], ex["sheet"], rec_last, now_last)
        self.check_errors(ws)
        return f"{base + 1}행에 입력"

    def op_cells(self, ex, value):
        ws = self.sheet(ex["sheet"])
        ws.Activate()
        rec_last = ex.get("last_rows", {})
        now_last = self.last_rows(rec_last.keys())
        for cell in ex["cells"]:
            ws.Range(cell["addr"]).Formula = adjust_formula(cell["formula"], ex["sheet"], rec_last, now_last)
        self.check_errors(ws)
        return f"{len(ex['cells'])}칸"

    def op_add_sheet(self, ex, value):
        name = value or ex["sheet"]
        try:
            self.wb.Worksheets(name)
            raise StopRun("action_failed", f"이미 '{name}' 시트가 있습니다")
        except StopRun:
            raise
        except Exception:
            pass
        ws = self.wb.Worksheets.Add(None, self.wb.Worksheets(self.wb.Worksheets.Count))
        ws.Name = name
        return ""

    def op_rename_sheet(self, ex, value):
        ws = self.sheet(ex["old"])
        ws.Name = value or ex["sheet"]
        self.sheet_map[ex["old"]] = ws.Name
        return ""

    def op_delete_sheet(self, ex, value):
        self.sheet(ex["sheet"]).Delete()
        return ""


def run_excel_macro(macro: dict, macro_path: str | Path, values: dict[str, str], render,
                    headless: bool = False, checks: bool = True,
                    log: Callable[[str], None] = print) -> dict:
    """엑셀 매크로를 실행하고 결과 dict를 돌려준다 (웹 매크로와 같은 result.json 형식)."""
    run_dir = new_run_dir(macro["id"])
    result = new_result("macro", str(macro_path), run_dir)
    result["kind"] = "excel"
    result["version"] = "v4" if checks else "v3"
    result["variables"] = dict(values)
    result["total"] = len(macro["steps"])
    visible = not headless

    xl = wb = runner = None
    stem = "결과"
    saved = False
    try:
        for step in macro["steps"]:
            n, action = step["step"], step["action"]
            value = render(step.get("value", ""), values)
            rec = {"step": n, "action": action, "target": step["target"], "value": value,
                   "status": "성공", "message": ""}
            try:
                op = step.get("excel", {}).get("op")
                if op == "open":
                    src = resolve_file(value)
                    if src is None:
                        raise StopRun("file_missing", value)
                    stem = src.stem
                    work = run_dir / src.name
                    shutil.copy2(src, work)
                    log(f"     원본: {src} (복사본에서 작업)")
                    xl = start_excel(visible=visible)
                    wb = xl.Workbooks.Open(str(work))
                    if visible:
                        try:
                            xl.WindowState = -4137
                        except Exception:
                            pass
                    sheets = step["excel"].get("sheets", {})
                    runner = ExcelRunner(xl, wb, next(iter(sheets), ""), checks)
                    rec["message"] = f"{src.name} 열기"
                    if checks:
                        rec["message"] += ", " + runner.check_structure(sheets)
                    rec["message"] = runner.note + rec["message"]
                elif op == "save":
                    out = run_dir / f"{stem}_결과.xlsx"
                    wb.SaveAs(str(out), XLSX_FORMAT)
                    saved = True
                    result["downloads"].append(out.name)
                    rec["message"] = f"저장: {out.name}"
                else:
                    if runner is None:
                        raise StopRun("file_missing", "열린 파일 없음")
                    rec["message"] = runner.run(step, value)
            except StopRun as e:
                rec["status"] = "실패"
                rec["message"] += f" {STOP_REASONS[e.code]} [{e.detail}]"
                rec["reason"] = e.code
            except Exception as e:
                rec["status"] = "실패"
                rec["message"] += f"{STOP_REASONS['action_failed']} ({str(e).splitlines()[0][:200]})"
                rec["reason"] = "action_failed"

            if rec["status"] == "실패":
                if xl is not None and visible:
                    shot = f"fail_step{n:03d}.jpg"
                    if capture_window(xl.Hwnd, run_dir / shot):
                        rec["screenshot"] = shot
                result.update(stopped_at=n, stop_reason=rec["reason"],
                              stop_message=f"{rec['message']} {HUMAN_HANDOFF}")
            result["steps"].append(rec)
            log(f"[{n:02d}] {rec['status']:<2} {action:<5} {step['target']} {rec['message']}")
            if rec["status"] == "실패":
                log(f"\n⛔ {n}단계에서 멈췄습니다: {rec['message']}")
                log(f"   {HUMAN_HANDOFF}")
                break
            if visible and xl is not None:
                time.sleep(STEP_DELAY)

        # 멈췄으면 그때까지 한 작업을 따로 저장 (사람이 이어서 처리)
        if wb is not None and not saved:
            part = run_dir / f"{stem}_중단.xlsx"
            try:
                wb.SaveAs(str(part), XLSX_FORMAT)
                result["downloads"].append(part.name)
                log(f"   중간 결과: {part.name}")
            except Exception:
                pass
    finally:
        try:
            if wb is not None:
                wb.Close(False)
        except Exception:
            pass
        try:
            if xl is not None:
                xl.Quit()
        except Exception:
            pass

    finish_result(result)
    save_result(run_dir, result)
    log(f"총 {result['total']}단계 중 {result['succeeded']}단계 성공 → {run_dir / 'result.json'}")
    return result
