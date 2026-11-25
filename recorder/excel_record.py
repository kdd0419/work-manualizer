"""엑셀 업무 녹화기 (실제 Microsoft Excel 연동).

실행:
  python -m recorder.excel_record --file samples/위탁업체점검_2026-10.xlsx --name 부적합정리
  → Excel 창에서 업무를 시연하고, 통합 문서(또는 Excel)를 닫으면 녹화 종료

녹화 방식:
  Excel 메뉴 클릭 자체는 밖에서 기록할 수 없으므로, 0.5초마다 통합 문서 상태(셀 수식·값, 필터, 시트 목록)를
  읽어 직전 상태와 비교한다. 바뀐 점을 아래 이벤트로 분류하고, 그때마다 Excel 창을 캡처한다.
    filter       필터 조건이 바뀜 (어느 열을 어떤 값으로)
    sort         행 내용은 같고 순서만 바뀜 → 어느 열 기준 오름/내림차순인지 추정
    delete_rows  일부 행이 통째로 사라짐 (사라진 행과 남은 행을 함께 기록 → 해석 때 삭제 조건 추정)
    cells        셀 값·수식이 바뀜 (머리글/데이터/표 아래 위치와 행 열쇠값을 함께 기록)
    add_sheet / delete_sheet / rename_sheet

원본 파일은 건드리지 않는다: recordings/<id>/input.<확장자>(원본 복사본)와 work.<확장자>(작업본)를 만들고 작업본을 연다.

저장:
  recordings/<이름_타임스탬프>/events.json   ("kind": "excel")
  recordings/<...>/screenshots/001.jpg ...
"""
import argparse
import json
import shutil
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .excel_com import (ExcelBusy, capture_window, col_letter, headers, is_busy, snapshot,
                        start_excel)
from .record import RECORDINGS_DIR, safe_name

SUPPORTED = (".xlsx", ".xlsm", ".xls", ".csv")


# ------------------------------------------------------------------ 상태 비교 → 이벤트

def _sort_key(x):
    """정렬 비교용: 숫자 < 글자 < 빈 칸."""
    if x == "" or x is None:
        return (2, "")
    if isinstance(x, (int, float)):
        return (0, x)
    return (1, str(x))


def _monotonic(vals: list, desc: bool) -> bool:
    keys = [_sort_key(v) for v in vals if v != ""]
    if len(set(keys)) < 2:
        return False   # 값이 모두 같으면 정렬 기준으로 볼 수 없음
    pairs = zip(keys, keys[1:])
    return all(a >= b for a, b in pairs) if desc else all(a <= b for a, b in pairs)


def _infer_sort(prev: dict, cur: dict) -> tuple[int | None, str]:
    """순서만 바뀐 표에서 정렬 기준 열과 방향을 추정한다."""
    pv, cv = prev["v"][1:], cur["v"][1:]
    best = None
    for j in range(cur["last_col"]):
        col_now = [r[j] for r in cv]
        col_before = [r[j] for r in pv]
        for desc in (False, True):
            if _monotonic(col_now, desc):
                already = _monotonic(col_before, desc)
                cand = (already, j + 1, "내림차순" if desc else "오름차순")
                if best is None or cand < best:
                    best = cand
    return (best[1], best[2]) if best else (None, "")


def _is_subsequence(small: list, big: list) -> list[int] | None:
    """small이 big의 순서를 지킨 부분 목록이면, big에서 빠진 위치들을 돌려준다."""
    removed, i = [], 0
    for j, row in enumerate(big):
        if i < len(small) and small[i] == row:
            i += 1
        else:
            removed.append(j)
    return removed if i == len(small) else None


def _key_col(sheet: dict) -> int | None:
    """데이터 행마다 값이 모두 다르고 비어 있지 않은 첫 열 (행을 찾는 열쇠). 없으면 None."""
    rows = sheet["v"][1:]
    if len(rows) < 2:
        return None
    for j in range(sheet["last_col"]):
        vals = [r[j] if j < len(r) else "" for r in rows]
        if all(v != "" for v in vals) and len(set(map(str, vals))) == len(vals):
            return j + 1
    return None


def _cell(grid: list[list], r: int, c: int):
    """1부터 세는 (행, 열)의 값. 범위 밖이면 ''."""
    if r - 1 < len(grid) and c - 1 < len(grid[r - 1]):
        return grid[r - 1][c - 1]
    return ""


def _empty_sheet(name: str) -> dict:
    return {"name": name, "f": [], "r1c1": [], "v": [], "last_row": 0, "last_col": 0,
            "filters": {}, "visible": None}


def diff_sheet(prev: dict, cur: dict, created: bool, last_rows: dict) -> list[dict]:
    """시트 하나의 직전/현재 상태를 비교해 이벤트 목록을 만든다."""
    events: list[dict] = []
    name = cur["name"]
    head = headers(prev) if prev["f"] else headers(cur)

    # 1) 필터 조건 변화
    if prev["filters"] != cur["filters"]:
        cols = sorted(set(prev["filters"]) | set(cur["filters"]))
        for c in cols:
            if prev["filters"].get(c) != cur["filters"].get(c):
                vis = cur["visible"]
                events.append({"type": "filter", "sheet": name, "col": c,
                               "header": _cell(cur["f"], 1, c), "cond": cur["filters"].get(c),
                               "visible_count": len(vis) if vis is not None else max(cur["last_row"] - 1, 0),
                               "total_rows": max(cur["last_row"] - 1, 0)})

    if prev["f"] == cur["f"]:
        return events

    same_header = prev["f"][:1] == cur["f"][:1] and prev["last_col"] == cur["last_col"]
    prow = [tuple(r) for r in prev["f"][1:]]
    crow = [tuple(r) for r in cur["f"][1:]]

    # 2) 정렬: 행 묶음은 같고 순서만 다름
    if same_header and len(prow) == len(crow) and prow != crow and \
            sorted(map(repr, prow)) == sorted(map(repr, crow)):
        col, order = _infer_sort(prev, cur)
        events.append({"type": "sort", "sheet": name, "col": col,
                       "header": _cell(cur["f"], 1, col) if col else "", "order": order})
        return events

    # 3) 행 삭제: 남은 행이 원래 행 순서를 지킨 부분 목록
    if same_header and len(crow) < len(prow):
        removed = _is_subsequence(crow, prow)
        if removed:
            pv = prev["v"][1:]
            kept = [pv[j] for j in range(len(pv)) if j not in set(removed)]
            events.append({"type": "delete_rows", "sheet": name, "header": head,
                           "deleted": [pv[j] for j in removed], "kept": kept,
                           "deleted_rows": [j + 2 for j in removed], "last_row_before": prev["last_row"]})
            return events

    # 4) 셀 변경
    L = prev["last_row"]
    changes = []
    nr = max(prev["last_row"], cur["last_row"])
    nc = max(prev["last_col"], cur["last_col"])
    key_col = _key_col(prev) if not created else None
    for r in range(1, nr + 1):
        for c in range(1, nc + 1):
            old, new = _cell(prev["f"], r, c), _cell(cur["f"], r, c)
            if old == new:
                continue
            if created or L == 0:
                area = "free"
            elif r == 1:
                area = "header"
            elif r <= L:
                area = "data"
            else:
                area = "below"
            ch = {"row": r, "col": c, "addr": f"{col_letter(c)}{r}", "area": area,
                  "header": _cell(cur["f"], 1, c) if area != "free" else "",
                  "old": old, "new": new, "value": _cell(cur["v"], r, c)}
            if isinstance(new, str) and new.startswith("="):
                ch["r1c1"] = _cell(cur["r1c1"], r, c)
            if area == "data" and key_col:
                ch["key"] = _cell(prev["v"], r, key_col)
            if area == "below":
                ch["offset"] = r - L
            changes.append(ch)
    if changes:
        ev = {"type": "cells", "sheet": name, "created": created, "header": head,
              "last_row_before": L, "last_rows": last_rows, "visible": prev["visible"],
              "key_col": key_col, "key_header": _cell(prev["f"], 1, key_col) if key_col else "",
              "changes": changes}
        if any(ch["area"] == "data" for ch in changes):
            ev["table"] = prev["v"][1:]   # 조건 추정용 (변경 전 데이터 행)
        events.append(ev)
    return events


def diff(prev: dict, cur: dict, created_sheets: set[str]) -> list[dict]:
    """통합 문서 전체 비교. created_sheets: 녹화 중 새로 만든 시트 이름 (갱신됨)."""
    events: list[dict] = []
    before, after = set(prev["order"]), set(cur["order"])
    added = [s for s in cur["order"] if s not in before]
    removed = [s for s in prev["order"] if s not in after]
    # 이름만 바뀐 시트: 내용이 같은 사라진 시트와 새 시트
    for a in list(added):
        for r in list(removed):
            if prev["sheets"][r]["f"] == cur["sheets"][a]["f"]:
                events.append({"type": "rename_sheet", "sheet": a, "old": r})
                added.remove(a)
                removed.remove(r)
                if r in created_sheets:
                    created_sheets.discard(r)
                    created_sheets.add(a)
                prev = {**prev, "sheets": {**prev["sheets"], a: {**prev["sheets"][r], "name": a}}}
                break
    for r in removed:
        events.append({"type": "delete_sheet", "sheet": r})
    for a in added:
        events.append({"type": "add_sheet", "sheet": a})
        created_sheets.add(a)
    last_rows = {n: s["last_row"] for n, s in prev["sheets"].items()}
    for name in cur["order"]:
        p = prev["sheets"].get(name) or _empty_sheet(name)
        events += diff_sheet(p, cur["sheets"][name], name in created_sheets, last_rows)
    return events


def _changed(a: dict, b: dict) -> bool:
    if a["order"] != b["order"]:
        return True
    return any(a["sheets"][n]["f"] != b["sheets"][n]["f"] or a["sheets"][n]["filters"] != b["sheets"][n]["filters"]
               for n in a["order"])


def _summary(ev: dict) -> str:
    """로그 한 줄."""
    t = ev["type"]
    if t == "filter":
        return f"필터 [{ev['sheet']}] {ev['header']}: {ev['cond'] or '해제'} → {ev['visible_count']}/{ev['total_rows']}행"
    if t == "sort":
        return f"정렬 [{ev['sheet']}] {ev['header'] or '?'} {ev['order']}"
    if t == "delete_rows":
        return f"행 삭제 [{ev['sheet']}] {len(ev['deleted'])}행"
    if t == "cells":
        addrs = [c["addr"] for c in ev["changes"]]
        return f"셀 변경 [{ev['sheet']}] {', '.join(addrs[:6])}{' …' if len(addrs) > 6 else ''} ({len(addrs)}칸)"
    if t == "rename_sheet":
        return f"시트 이름 변경 {ev['old']} → {ev['sheet']}"
    return {"add_sheet": "시트 추가", "delete_sheet": "시트 삭제"}.get(t, t) + f" {ev.get('sheet', '')}"


# ------------------------------------------------------------------ 녹화

def _rel(p: Path) -> str:
    """프로젝트 폴더 안이면 상대 경로(samples/…), 아니면 절대 경로."""
    root = RECORDINGS_DIR.parent
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return str(p)


def record_excel(file: str | Path, name: str, poll: float = 0.5, wait_stdin: bool = True,
                 script: list[Callable] | None = None, visible: bool = True,
                 log: Callable[[str], None] = print) -> Path:
    """엑셀 파일 하나를 Excel로 열어 녹화한다. 통합 문서를 닫으면 끝난다.

    script: 자동 시연용 (사람 대신 순서대로 실행할 함수 목록, 각 함수는 (xl, wb)를 받음). 평가·테스트용.
    """
    src = Path(file).resolve()
    if not src.exists():
        raise FileNotFoundError(f"파일이 없습니다: {src}")
    if src.suffix.lower() not in SUPPORTED:
        raise ValueError(f"지원하지 않는 형식입니다: {src.suffix} (가능: {', '.join(SUPPORTED)})")

    started = datetime.now()
    rec_id = f"{safe_name(name)}_{started:%Y%m%d_%H%M%S}"
    out_dir = RECORDINGS_DIR / rec_id
    shot_dir = out_dir / "screenshots"
    shot_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, out_dir / f"input{src.suffix.lower()}")
    work = out_dir / f"work{src.suffix.lower()}"
    shutil.copy2(src, work)

    log(f"녹화 시작: {rec_id}")
    log(f"원본 파일: {src.name} (원본은 바꾸지 않고 복사본을 엽니다)")
    xl = start_excel(visible=visible)
    wb = xl.Workbooks.Open(str(work))
    try:
        xl.WindowState = -4137   # 최대화 (캡처가 잘 보이도록)
        wb.Activate()
    except Exception:
        pass
    log("Excel 창에서 업무를 시연하세요. 끝나면 통합 문서(또는 Excel)를 닫으면 녹화가 끝납니다.")

    events: list[dict] = []
    stop = threading.Event()
    if wait_stdin and script is None:
        def wait_enter():
            try:
                input()
                stop.set()
            except EOFError:   # 관리 화면에서 실행하면 입력이 없음 → Excel을 닫을 때까지 녹화
                pass
        threading.Thread(target=wait_enter, daemon=True).start()

    def add(ev: dict, shot: str) -> None:
        ev = {"seq": len(events) + 1, "time": datetime.now().isoformat(timespec="seconds"), **ev,
              "screenshot": shot}
        events.append(ev)
        log(f"  [{ev['seq']:03d}] {_summary(ev)}")

    def take_shot(seq: int) -> str:
        time.sleep(0.25)   # 화면이 다시 그려질 시간
        rel = f"screenshots/{seq:03d}.jpg"
        try:
            ok = capture_window(xl.Hwnd, out_dir / rel)
        except Exception:
            ok = False
        return rel if ok else ""

    import pythoncom
    prev = None
    while prev is None:
        try:
            prev = snapshot(wb)
        except ExcelBusy:
            time.sleep(poll)
    first = {"type": "open", "file": src.name,
             "sheets": {n: {"headers": headers(s), "last_row": s["last_row"]} for n, s in prev["sheets"].items()},
             "active": prev["active"]}
    add(first, take_shot(1))

    created: set[str] = set()
    fails = 0
    steps = list(script or [])
    while not stop.is_set():
        pythoncom.PumpWaitingMessages()
        time.sleep(poll)
        if script is not None:
            if steps:
                try:
                    steps.pop(0)(xl, wb)
                except Exception as e:
                    log(f"  (자동 시연 단계 실패: {e})")
            else:
                try:
                    wb.Close(False)
                except Exception:
                    pass
        try:
            cur = snapshot(wb)
            fails = 0
        except ExcelBusy:
            continue
        except Exception as e:
            if is_busy(e):
                continue
            fails += 1
            if fails >= 2:   # 통합 문서나 Excel이 닫힘
                break
            continue
        if _changed(prev, cur):
            new_events = diff(prev, cur, created)
            if new_events:
                shot = take_shot(len(events) + 1)
                for ev in new_events:
                    add(ev, shot)
            prev = cur

    try:
        if xl.Workbooks.Count == 0:
            xl.Quit()
    except Exception:
        pass

    ended = datetime.now()
    meta = {
        "id": rec_id,
        "name": name,
        "kind": "excel",
        "source_file": src.name,
        "source_path": _rel(src),
        "input_file": f"input{src.suffix.lower()}",
        "start_url": "",
        "started_at": started.isoformat(timespec="seconds"),
        "ended_at": ended.isoformat(timespec="seconds"),
        "event_count": len(events),
        "events": events,
    }
    (out_dir / "events.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"녹화 종료: 이벤트 {len(events)}개 → {out_dir / 'events.json'}")
    return out_dir


def main() -> None:
    """명령줄 실행."""
    parser = argparse.ArgumentParser(description="엑셀 업무 녹화")
    parser.add_argument("--file", required=True, help="녹화할 엑셀 파일 (원본은 바뀌지 않음)")
    parser.add_argument("--name", required=True, help="녹화 이름")
    parser.add_argument("--poll", type=float, default=0.5, help="상태 확인 간격(초)")
    args = parser.parse_args()
    try:
        record_excel(args.file, args.name, poll=args.poll)
    except (FileNotFoundError, ValueError) as e:
        print(f"오류: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
