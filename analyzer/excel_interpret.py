"""엑셀 녹화(events.json, kind=excel)를 업무 단계(steps.json)로 해석한다.

analyzer.interpret가 녹화 종류를 보고 이 모듈을 부른다.

핵심: 녹화 파일의 '몇 번째 행'이 아니라, 다른 파일에도 그대로 적용할 수 있는 규칙으로 바꾼다.
  - 필터로 보이는 행에만 같은 값 입력   → '필터로 보이는 행의 담당자 열 채우기'
  - 사라진 행이 모두 점검결과='적합'      → '점검결과가 적합인 행 삭제' (남은 행에는 '적합'이 없을 때만)
  - 표 바로 아래 행에 입력한 합계 수식    → '표 아래 행 추가' (실행 파일의 행 수에 맞춰 범위 자동 확장)
  - 데이터 행 한 칸만 고침               → 열쇠 열(점검번호 등) 값으로 행을 찾아 입력

역할 분담: 동작 종류·대상·값·조건은 코드가 기록에서 정한다. AI는 단계 설명 문장만 다듬는다.
"""
from pathlib import Path

from recorder.excel_com import cond_text

from .common import fill_prompt, load_prompt, now_iso, write_json
from .josa import fix_josa

MAX_COND_VALUES = 5   # 조건으로 인정할 값 종류 수 (너무 많으면 조건이 아니라 우연)


def _s(v) -> str:
    return "" if v is None else str(v)


def _short(v, n: int = 40) -> str:
    v = _s(v)
    return v if len(v) <= n else v[:n] + "…"


def infer_condition(header: list, chosen: list[list], others: list[list], skip: set[int]) -> dict | None:
    """chosen 행들에만 있고 others에는 없는 값을 가진 열을 찾아 조건으로 만든다.

    돌려주는 값: {"col": 열 번호(1부터), "header": 열 이름, "values": [...]} 또는 None
    """
    best = None
    ncol = max((len(r) for r in chosen + others), default=0)
    for j in range(ncol):
        if j + 1 in skip:
            continue
        cv = {_s(r[j] if j < len(r) else "") for r in chosen}
        ov = {_s(r[j] if j < len(r) else "") for r in others}
        if not cv or cv & ov or len(cv) > MAX_COND_VALUES:
            continue
        if len(chosen) > 1 and len(cv) == len(chosen):
            continue   # 행마다 값이 다르면 (번호 열 등) 조건이 아님
        cand = (len(cv), j)
        if best is None or cand < best[0]:
            best = (cand, j, sorted(cv))
    if best is None:
        return None
    _, j, vals = best
    return {"col": j + 1, "header": _s(header[j]) if j < len(header) else "", "values": vals}


def _merge_cells_events(events: list[dict]) -> list[dict]:
    """같은 시트의 연속된 변경을 하나로 합친다 (한 칸씩 입력한 것, 여러 번 나눠 지운 행 묶기).

    - 데이터 행의 같은 열을 연달아 고친 경우 (예: 보이는 행마다 담당자 입력)
    - 새로 만든 시트에 칸을 연달아 입력한 경우 (요약표 작성)
    """
    out: list[dict] = []
    for ev in events:
        # 연속된 행 삭제(여러 번에 나눠 지움)는 하나로: 지운 행은 합치고, 남은 행은 마지막 상태
        if ev["type"] == "delete_rows" and out and out[-1]["type"] == "delete_rows" \
                and out[-1]["sheet"] == ev["sheet"] and out[-1]["header"] == ev["header"]:
            last = out[-1]
            out[-1] = {**last, "deleted": last["deleted"] + ev["deleted"], "kept": ev["kept"],
                       "deleted_rows": last["deleted_rows"] + ev["deleted_rows"],
                       "screenshot": ev.get("screenshot") or last.get("screenshot")}
            continue
        if ev["type"] != "cells" or not out or out[-1]["type"] != "cells" or out[-1]["sheet"] != ev["sheet"]:
            out.append(ev)
            continue
        last = out[-1]
        la = {c["area"] for c in last["changes"]}
        ea = {c["area"] for c in ev["changes"]}
        same_col = len({c["col"] for c in last["changes"] + ev["changes"]}) == 1
        if (la == ea == {"data"} and same_col) or (la == ea == {"free"}):
            merged = {(c["row"], c["col"]): c for c in last["changes"]}
            for c in ev["changes"]:
                old = merged.get((c["row"], c["col"]))
                merged[(c["row"], c["col"])] = {**c, "old": old["old"]} if old else c
            out[-1] = {**last, "changes": sorted(merged.values(), key=lambda c: (c["row"], c["col"])),
                       "screenshot": ev.get("screenshot") or last.get("screenshot")}
        else:
            out.append(ev)
    return out


def _step(ev: dict, action: str, target: str, value, description: str, excel: dict,
          variable_ok: bool = False, var_name: str = "") -> dict:
    s = {"event_seq": ev["seq"], "action": action, "target": target, "value": _s(value),
         "description": fix_josa(description), "changes_data": False, "locator": {}, "page_url": "",
         "screenshot": ev.get("screenshot", ""), "excel": excel}
    if variable_ok:
        s["variable_ok"] = True
        s["var_name"] = var_name or target
    return s


def _cells_steps(ev: dict) -> list[dict]:
    """셀 변경 이벤트 하나 → 단계 여러 개."""
    sheet = ev["sheet"]
    steps: list[dict] = []
    by_area: dict[str, list[dict]] = {}
    for c in ev["changes"]:
        by_area.setdefault(c["area"], []).append(c)
    base = {"sheet": sheet, "last_rows": ev.get("last_rows", {})}

    # 새로 만든 시트(또는 빈 시트)에 입력: 칸 주소 그대로
    if by_area.get("free"):
        cells = [{"addr": c["addr"], "formula": c["new"]} for c in by_area["free"]]
        preview = ", ".join(f"{c['addr']}: {_short(c['formula'], 25)}" for c in cells[:4])
        steps.append(_step(ev, "셀 입력", f"{sheet} 시트", f"{len(cells)}칸",
                           f"'{sheet}' 시트에 {len(cells)}칸을 입력한다 ({preview}{' …' if len(cells) > 4 else ''})",
                           {**base, "op": "cells", "cells": cells}))

    # 머리글: 새 열 만들기 / 머리글 이름 바꾸기
    for c in by_area.get("header", []):
        if c["old"] == "" and c["new"] != "":
            steps.append(_step(ev, "열 추가", f"{c['new']} 열", c["new"],
                               f"'{c['new']}' 열을 새로 만든다 (표 오른쪽 끝)",
                               {**base, "op": "add_column", "header": c["new"]}))
        else:
            steps.append(_step(ev, "머리글 변경", f"{c['old']} 열", c["new"],
                               f"'{c['old']}' 열 이름을 '{c['new']}'(으)로 바꾼다",
                               {**base, "op": "rename_header", "old": c["old"], "header": c["new"]}))

    # 데이터 행: 열 단위로 묶어 규칙 찾기
    data = by_area.get("data", [])
    by_col: dict[int, list[dict]] = {}
    for c in data:
        by_col.setdefault(c["col"], []).append(c)
    L = ev.get("last_row_before", 0)
    table = ev.get("table") or []
    head = ev.get("header") or []
    for col, cs in by_col.items():
        header = cs[0]["header"] or f"{cs[0]['addr'][:-len(str(cs[0]['row']))]}열"
        is_formula = isinstance(cs[0]["new"], str) and cs[0]["new"].startswith("=")
        same = len({(c.get("r1c1") if is_formula else _s(c["new"])) for c in cs}) == 1
        rows = {c["row"] for c in cs}
        all_rows = set(range(2, L + 1))
        scope = None
        if same and len(cs) > 1 or (same and len(all_rows) == 1):
            if rows == all_rows:
                scope = {"type": "all"}
            elif ev.get("visible") is not None and rows == set(ev["visible"]):
                scope = {"type": "visible"}
            elif table:
                chosen = [table[r - 2] for r in sorted(rows) if r - 2 < len(table)]
                others = [table[i] for i in range(len(table)) if i + 2 not in rows]
                cond = infer_condition(head, chosen, others, {col, ev.get("key_col") or 0})
                if cond:
                    scope = {"type": "where", **cond}
        if scope:
            val = cs[0]["new"]
            where = {"all": "모든 행", "visible": "필터로 보이는 행",
                     "where": f"'{scope.get('header')}'이(가) '{', '.join(scope.get('values', []))}'인 행"}[scope["type"]]
            what = f"수식 {val}" if is_formula else f"'{val}'"
            ex = {**base, "op": "fill", "header": header, "scope": scope, "rec_row": cs[0]["row"],
                  "rec_count": len(cs)}
            if is_formula:
                ex.update(formula=val, r1c1=cs[0].get("r1c1", ""))
            steps.append(_step(ev, "열 채우기", f"{header} 열", val,
                               f"{where}의 '{header}' 열에 {what}을(를) 채운다", ex,
                               variable_ok=not is_formula, var_name=header))
            continue
        # 규칙이 없으면 한 칸씩: 열쇠 열 값으로 행을 찾는다
        for c in cs:
            key = c.get("key")
            is_f = isinstance(c["new"], str) and c["new"].startswith("=")
            ex = {**base, "op": "set", "header": header, "key_header": ev.get("key_header", ""), "key": key,
                  "addr": c["addr"], "rec_row": c["row"]}
            if is_f:
                ex.update(formula=c["new"], r1c1=c.get("r1c1", ""))
            where = f"{ev.get('key_header')} '{key}' 행" if key not in (None, "") else f"{c['addr']} 칸"
            steps.append(_step(ev, "값 입력", f"{where}의 {header}", c["new"],
                               f"{where}의 '{header}' 칸에 값을 입력한다", ex,
                               variable_ok=not is_f, var_name=header))

    # 표 바로 아래 행 (합계 행 등)
    below = by_area.get("below", [])
    if below:
        cells = [{"header": c["header"], "col": c["col"], "offset": c["offset"], "formula": c["new"]} for c in below]
        preview = ", ".join(f"{c['header'] or c['col']}: {_short(c['formula'], 25)}" for c in cells[:4])
        steps.append(_step(ev, "아래 행 추가", "표 아래", f"{len(cells)}칸",
                           f"표 바로 아래에 행을 추가해 입력한다 ({preview}). 범위는 실행 파일의 행 수에 맞춰 늘어남",
                           {**base, "op": "below", "cells": cells}))
    return steps


def _event_steps(ev: dict, meta: dict) -> list[dict]:
    """이벤트 하나 → 단계 목록 (규칙 기반)."""
    t = ev["type"]
    if t == "open":
        path = meta.get("source_path") or meta.get("source_file", "")
        return [_step(ev, "파일 열기", ev["file"], path, f"엑셀 파일 '{ev['file']}'을(를) 연다",
                      {"op": "open", "sheets": ev["sheets"], "active": ev.get("active")},
                      variable_ok=True, var_name="입력파일")]
    if t == "filter":
        h = ev["header"]
        base = {"sheet": ev["sheet"], "header": h}
        if not ev.get("cond"):
            return [_step(ev, "필터 해제", f"{h} 열", "", f"'{h}' 열의 필터를 해제한다",
                          {**base, "op": "unfilter"})]
        cond = ev["cond"]
        text = cond_text(cond)
        note = f" (녹화 때 {ev['total_rows']}행 중 {ev['visible_count']}행)"
        return [_step(ev, "필터", f"{h} 열", text, f"'{h}' 열을 '{text}'(으)로 필터링한다{note}",
                      {**base, "op": "filter", "cond": cond},
                      variable_ok=cond.get("type") == "values", var_name=f"{h}_필터")]
    if t == "sort":
        h = ev["header"] or "?"
        return [_step(ev, "정렬", f"{h} 열", ev["order"], f"'{h}' 열 기준으로 {ev['order']} 정렬한다",
                      {"sheet": ev["sheet"], "op": "sort", "header": ev["header"], "desc": ev["order"] == "내림차순"})]
    if t == "delete_rows":
        head = ev["header"]
        key_col = None
        rows_all = ev["deleted"] + ev["kept"]
        for j in range(len(head)):
            vals = [_s(r[j]) if j < len(r) else "" for r in rows_all]
            if all(vals) and len(set(vals)) == len(vals):
                key_col = j + 1
                break
        cond = infer_condition(head, ev["deleted"], ev["kept"], {key_col or 0})
        n = len(ev["deleted"])
        if cond:
            text = ", ".join(v if v else "(빈 칸)" for v in cond["values"])
            return [_step(ev, "행 삭제", f"{cond['header']} 열", text,
                          f"'{cond['header']}'이(가) '{text}'인 행을 모두 삭제한다 (녹화 때 {n}행)",
                          {"sheet": ev["sheet"], "op": "delete_where", "header": cond["header"],
                           "cond": {"type": "values", "values": cond["values"]}},
                          variable_ok=True, var_name=f"{cond['header']}_삭제조건")]
        keys = [_s(r[key_col - 1]) for r in ev["deleted"]] if key_col else []
        return [_step(ev, "행 삭제", "지정한 행", f"{n}행",
                      f"녹화 때 고른 {n}개 행을 삭제한다 (공통 조건을 찾지 못해 행을 하나씩 지정)",
                      {"sheet": ev["sheet"], "op": "delete_keys", "key_header": head[key_col - 1] if key_col else "",
                       "keys": keys, "rows": ev["deleted_rows"]})]
    if t == "cells":
        return _cells_steps(ev)
    if t == "add_sheet":
        return [_step(ev, "시트 추가", ev["sheet"], ev["sheet"], f"'{ev['sheet']}' 시트를 새로 만든다",
                      {"op": "add_sheet", "sheet": ev["sheet"]})]
    if t == "rename_sheet":
        return [_step(ev, "시트 이름 변경", ev["old"], ev["sheet"], f"'{ev['old']}' 시트 이름을 '{ev['sheet']}'(으)로 바꾼다",
                      {"op": "rename_sheet", "old": ev["old"], "sheet": ev["sheet"]})]
    if t == "delete_sheet":
        return [_step(ev, "시트 삭제", ev["sheet"], "", f"'{ev['sheet']}' 시트를 삭제한다",
                      {"op": "delete_sheet", "sheet": ev["sheet"]})]
    return []


def _ai_descriptions(name: str, steps: list[dict], prompt_version: str, log) -> dict[int, str]:
    """AI로 단계 설명 문장만 다듬는다. 실패해도 규칙 기반 설명을 그대로 쓴다."""
    from .llm_client import generate_json
    view = [{"step": s["step"], "action": s["action"], "target": s["target"], "value": s["value"],
             "description": s["description"]} for s in steps]
    try:
        res = generate_json(fill_prompt(load_prompt("excel_interpret", prompt_version),
                                        RECORDING_NAME=name, STEPS=view))
    except Exception as e:
        log(f"AI 설명 다듬기 실패 → 규칙 기반 설명 사용 ({e})")
        return {}
    out = {}
    for s in (res.get("steps", []) if isinstance(res, dict) else []):
        if isinstance(s, dict) and isinstance(s.get("step"), int) and str(s.get("description", "")).strip():
            out[s["step"]] = str(s["description"]).strip()
    return out


def interpret_excel(rec_dir: Path, data: dict, use_ai: bool, prompt_version: str, log) -> Path:
    """엑셀 녹화 → steps.json"""
    events = _merge_cells_events(data["events"])
    log(f"이벤트 {len(data['events'])}개 → 묶은 뒤 {len(events)}개")
    steps: list[dict] = []
    for ev in events:
        steps += _event_steps(ev, data)
    last = events[-1] if events else {"seq": 0}
    src = data.get("source_file", "input.xlsx")
    stem = src.rsplit(".", 1)[0]
    steps.append(_step({"seq": last["seq"], "screenshot": last.get("screenshot", "")}, "저장", "결과 파일",
                       f"{stem}_결과.xlsx", "작업 결과를 새 엑셀 파일(…_결과.xlsx)로 저장한다. 원본 파일은 바뀌지 않는다",
                       {"op": "save"}))
    for i, s in enumerate(steps, 1):
        s["step"] = i

    if use_ai:
        log("AI 단계 설명 요청")
        desc = _ai_descriptions(data.get("name", ""), steps, prompt_version, log)
        for s in steps:
            if s["step"] in desc:
                s["description"] = fix_josa(desc[s["step"]])
            else:
                s["note"] = "AI 응답에 없어 규칙 기반으로 채움"

    from .llm_client import model_name
    out = {
        "recording_id": data["id"],
        "recording_name": data.get("name", ""),
        "kind": "excel",
        "source_file": src,
        "source_path": data.get("source_path", ""),
        "start_url": "",
        "generated_at": now_iso(),
        "model": model_name() if use_ai else "규칙 기반(AI 미사용)",
        "prompt_version": prompt_version if use_ai else None,
        "steps": steps,
        "dropped": [],
    }
    path = write_json(rec_dir / "steps.json", out)
    for s in steps:
        log(f"  {s['step']:>2}. [{s['action']}] {s['description']}")
    log(f"단계 {len(steps)}개 → {path}")
    return path
