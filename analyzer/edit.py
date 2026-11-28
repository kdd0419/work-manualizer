"""말로 고치기 (AI 기능 4): 녹화 없이 요청 문장으로 매뉴얼·매크로 변경안을 만들고, 검증·시험 실행 후 적용한다.

흐름
  1) AI가 변경안을 낸다 (feasible: yes / needs_recording / unclear)
  2) 코드가 검증한다: 바꿀 수 있는 항목만, 엑셀은 열 이름이 실제로 있는지·수식 모양, 바뀐 수식의 R1C1 다시 계산
  3) 같은 화면의 다른 항목으로 바꾸는 웹 단계(⑤): 매크로를 그 단계 직전까지 숨김 실행해 화면 구조를 읽고, AI가 항목을 고름
  4) 바꾼 매크로를 숨김으로 시험 실행한다 (엑셀: 녹화 때 파일, 웹: 데이터 변경 단계 직전까지)
  5) 사람이 비교 화면을 보고 '적용'을 눌러야 반영 (매크로·매뉴얼 모두 이전 버전 보관)
  다른 사이트처럼 녹화가 필요한 변경(⑥)은 적용하지 않고 대안(부분 재녹화 + 업무 연결)을 안내한다.

실행: python -m analyzer.edit <녹화id> --request-file 요청.json [--values 값.json]   (요청·값 파일은 읽은 뒤 지움)
결과: recordings/<id>/edits/<시각>.json (마지막 줄 RESULT_JSON=경로)
"""
import argparse
import copy
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .common import MACROS_DIR, MANUALS_DIR, RECORDINGS_DIR, fill_prompt, load_prompt, now_iso, read_json, write_json
from .josa import fix_josa
from .manual import split_blocks

FIELDS = {"value", "description", "target", "excel.header", "excel.formula", "excel.cond.values",
          "excel.scope.header", "excel.scope.values"}
VAR = re.compile(r"\{\{(.+?)\}\}")
IMG_LINE = re.compile(r"^\s*!\[[^\]]*\]\([^)]+\)\s*$")
_CELL = re.compile(r"(?P<sheet>(?:'[^']+'|[^\s'!:=(),+\-*/&<>^%\"{};]+)!)?(?<![A-Za-z0-9_.])"
                   r"(?P<cd>\$?)(?P<c>[A-Z]{1,3})(?P<rd>\$?)(?P<r>\d+)(?![\d(A-Za-z_])")

# 화면에서 누를 수 있는·입력할 수 있는 항목 목록 (⑤ 화면 구조 읽기)
ELEMENTS_JS = r"""
() => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim().slice(0, 60);
  const css = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    const parts = []; let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.body) {
      if (cur.id) { parts.unshift('#' + CSS.escape(cur.id)); break; }
      let p = cur.tagName.toLowerCase(); const par = cur.parentElement;
      if (par) { const same = [...par.children].filter((c) => c.tagName === cur.tagName); if (same.length > 1) p += `:nth-of-type(${same.indexOf(cur) + 1})`; }
      parts.unshift(p); cur = par;
    }
    return parts.join(' > ');
  };
  const out = [];
  for (const el of document.querySelectorAll('a, button, input, select, textarea, [role=button]')) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height || el.type === 'hidden') continue;
    const label = (el.labels && el.labels[0] ? el.labels[0].innerText : '') || el.getAttribute('aria-label') || '';
    out.push({tag: el.tagName.toLowerCase(), id: el.id || '', name: el.getAttribute('name') || '',
      type: el.tagName === 'INPUT' ? (el.type || 'text') : '', label: clean(label),
      text: clean(el.tagName === 'INPUT' ? el.value : el.innerText),
      options: el.tagName === 'SELECT' ? [...el.options].map((o) => clean(o.text)).slice(0, 20) : undefined,
      selector: css(el)});
    if (out.length >= 120) break;
  }
  return out;
}
"""


# ------------------------------------------------------------------ 엑셀 도우미

def col_num(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def a1_to_r1c1(formula: str, row: int, col: int) -> str:
    """A1 수식을 (row, col) 칸 기준 R1C1로 바꾼다. 열 채우기는 R1C1로 모든 행에 넣기 때문."""
    if not isinstance(formula, str) or not formula.startswith("="):
        return formula
    parts = formula.split('"')
    for i in range(0, len(parts), 2):
        def fix(m: re.Match) -> str:
            r, c = int(m.group("r")), col_num(m.group("c"))
            rp = f"R{r}" if m.group("rd") else ("R" if r == row else f"R[{r - row}]")
            cp = f"C{c}" if m.group("cd") else ("C" if c == col else f"C[{c - col}]")
            return f"{m.group('sheet') or ''}{rp}{cp}"
        parts[i] = _CELL.sub(fix, parts[i])
    return '"'.join(parts)


def sheet_headers(macro: dict) -> dict[str, list[str]]:
    """녹화 파일의 시트별 머리글 + 매크로가 '열 추가'로 만드는 머리글."""
    heads: dict[str, list[str]] = {}
    for s in macro["steps"]:
        ex = s.get("excel") or {}
        if ex.get("op") == "open":
            for name, info in ex.get("sheets", {}).items():
                heads[name] = [str(h) for h in info.get("headers", [])]
        elif ex.get("op") == "add_column":
            heads.setdefault(ex.get("sheet", ""), []).append(ex["header"])
    return heads


def formula_ok(f: str) -> str:
    """수식 모양 검사. 문제가 있으면 설명, 없으면 ''."""
    if not str(f).startswith("="):
        return "수식은 '='로 시작해야 합니다."
    depth = 0
    for ch in re.sub(r'"[^"]*"', "", f):
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if depth < 0:
            break
    return "" if depth == 0 else "괄호 짝이 맞지 않습니다."


# ------------------------------------------------------------------ 변경 적용 (사본에)

def _var_of(step: dict) -> str | None:
    m = VAR.search(str(step.get("value", "")))
    return m.group(1) if m else None


def _set_value(macro: dict, step: dict, value: str) -> None:
    """단계 값 바꾸기. 값이 변수면 변수의 녹화값(기본값)을 바꾼다."""
    var = _var_of(step)
    if var:
        for v in macro["variables"]:
            if v["name"] == var:
                v["recorded_value"] = value
                if v.get("default_rule") not in ("직접 입력",) and v.get("type") != "file":
                    v["default_rule"] = "recorded"
    else:
        step["value"] = value


def _shown_value(macro: dict, step: dict) -> str:
    var = _var_of(step)
    if var:
        v = next((x for x in macro["variables"] if x["name"] == var), None)
        return str(v["recorded_value"]) if v else str(step.get("value", ""))
    return str(step.get("value", ""))


def apply_field(macro: dict, step: dict, field: str, value, heads: dict, problems: list[str]) -> tuple[str, str] | None:
    """한 항목 바꾸기. (전, 후) 값을 돌려준다. 바꿀 수 없으면 problems에 이유를 넣고 None."""
    n = step["step"]
    ex = step.get("excel") or {}
    if field.startswith("excel.") and not ex:
        problems.append(f"{n}단계는 엑셀 단계가 아니라 '{field}'를 바꿀 수 없습니다.")
        return None
    sheet = ex.get("sheet", "")
    known = heads.get(sheet) or next(iter(heads.values()), [])
    if field == "value":
        before = _shown_value(macro, step)
        _set_value(macro, step, str(value))
        if ex.get("op") in ("filter", "delete_where") and ex.get("cond", {}).get("type") == "values":
            ex["cond"]["values"] = [x.strip() for x in str(value).split(",")]
        if ex.get("op") == "sort":
            ex["desc"] = str(value).strip() == "내림차순"
        return before, str(value)
    if field in ("description", "target"):
        before = step.get(field, "")
        step[field] = fix_josa(str(value))
        return before, step[field]
    if field == "excel.header":
        if str(value) not in known:
            problems.append(f"{n}단계: '{value}' 열이 녹화 파일에 없습니다 (있는 열: {', '.join(known)}).")
            return None
        before = ex.get("header", "")
        ex["header"] = str(value)
        return before, str(value)
    if field == "excel.formula":
        bad = formula_ok(str(value))
        if bad:
            problems.append(f"{n}단계 수식 '{value}': {bad}")
            return None
        if ex.get("op") not in ("fill", "set"):
            problems.append(f"{n}단계는 수식을 바꿀 수 있는 단계(열 채우기·값 입력)가 아닙니다.")
            return None
        header = ex.get("header", "")
        col = known.index(header) + 1 if header in known else None
        if col is None:
            problems.append(f"{n}단계: 수식을 넣을 '{header}' 열 위치를 알 수 없습니다.")
            return None
        before = ex.get("formula", step.get("value", ""))
        ex["formula"] = str(value)
        ex["r1c1"] = a1_to_r1c1(str(value), int(ex.get("rec_row") or 2), col)
        step["value"] = str(value)
        return before, str(value)
    if field == "excel.cond.values":
        vals = [str(x) for x in (value if isinstance(value, list) else str(value).split(","))]
        before = ", ".join(ex.get("cond", {}).get("values", []))
        ex.setdefault("cond", {"type": "values"})["values"] = [v.strip() for v in vals]
        _set_value(macro, step, ", ".join(v.strip() for v in vals))
        return before, ", ".join(vals)
    if field == "excel.scope.header":
        if str(value) not in known:
            problems.append(f"{n}단계: 조건 열 '{value}'이(가) 녹화 파일에 없습니다.")
            return None
        before = ex.get("scope", {}).get("header", "")
        ex.setdefault("scope", {"type": "where"}).update(type="where", header=str(value))
        return before, str(value)
    if field == "excel.scope.values":
        vals = [str(x).strip() for x in (value if isinstance(value, list) else str(value).split(","))]
        before = ", ".join(ex.get("scope", {}).get("values", []))
        ex.setdefault("scope", {"type": "where"}).update(type="where", values=vals)
        return before, ", ".join(vals)
    problems.append(f"'{field}'는 바꿀 수 없는 항목입니다.")
    return None


# ------------------------------------------------------------------ 웹: 화면 구조 읽기 (⑤)

def inspect_elements(macro_path: Path, values: dict, step_no: int) -> tuple[list[dict], str]:
    """매크로를 그 단계 직전까지 숨김 실행하고 화면의 항목 목록을 읽는다."""
    from runner.macro import run_macro
    box: dict = {}

    def grab(page):
        box["els"] = page.evaluate(ELEMENTS_JS)
        box["url"] = page.url
    res = run_macro(macro_path, values, headless=True, checks=True, allow_changes=False,
                    log=lambda *a: None, stop_before=step_no, on_stop=grab)
    if "els" not in box:
        why = res.get("stop_message") or "그 단계까지 가지 못했습니다"
        raise RuntimeError(f"{step_no}단계 화면까지 가지 못해 화면 구조를 읽지 못했습니다: {why}")
    return box["els"], box["url"]


def pick_element(request: str, hint: str, step: dict, elements: list[dict]) -> dict:
    from .llm_client import generate_json
    view = [{"index": i, **{k: v for k, v in e.items() if v}} for i, e in enumerate(elements)]
    res = generate_json(fill_prompt(load_prompt("edit_pick"), REQUEST=request, HINT=hint,
                                    STEP={k: step.get(k) for k in ("step", "action", "target", "value", "description")},
                                    ELEMENTS=view))
    return res if isinstance(res, dict) else {}


# ------------------------------------------------------------------ 변경안 만들기

def _ai_propose(name: str, macro: dict, blocks: list[str], request: str) -> dict:
    from .llm_client import generate_json
    steps = []
    for s in macro["steps"]:
        d = {"step": s["step"], "action": s["action"], "target": s["target"], "value": s.get("value", ""),
             "description": s.get("description", ""), "changes_data": s.get("changes_data", False)}
        ex = s.get("excel")
        if ex:
            d["excel"] = {k: ex[k] for k in ("op", "sheet", "header", "formula", "cond", "scope", "desc", "key_header")
                          if k in ex}
        else:
            loc = s.get("locator", {})
            d["page"] = urlparse(s.get("page_url", "")).path
            d["element"] = {k: loc[k] for k in ("tag", "id", "label", "text") if loc.get(k)}
        steps.append(d)
    view_blocks = [{"block": i, "text": "\n".join(l for l in b.splitlines() if not IMG_LINE.match(l)).strip()}
                   for i, b in enumerate(blocks)]
    res = generate_json(fill_prompt(load_prompt("edit"), RECORDING_NAME=name,
                                    KIND="엑셀" if macro.get("kind") == "excel" else "웹",
                                    HEADERS=sheet_headers(macro) or "(엑셀 아님)", STEPS=steps,
                                    BLOCKS=view_blocks or "(매뉴얼 없음)", REQUEST=request))
    return res if isinstance(res, dict) else {}


def _test_values(rid: str, macro: dict, given: dict) -> dict:
    """시험 실행 값: 사용자가 준 값 > 기본값. 엑셀 입력파일이 없으면 녹화 때 원본 복사본."""
    from runner.macro import resolve_variables
    from runner.excel_macro import resolve_file
    vals = {k: v for k, v in given.items() if k in {x["name"] for x in macro["variables"]} and str(v).strip()}
    for v in macro["variables"]:
        if v.get("type") == "file" and v["name"] not in vals and not resolve_file(str(v["recorded_value"])):
            inp = next((p for p in (RECORDINGS_DIR / rid).glob("input.*")), None)
            if inp:
                vals[v["name"]] = inp.relative_to(RECORDINGS_DIR.parent).as_posix()
    return resolve_variables(macro, vals)


def propose(rid: str, request: str, values: dict | None = None, log=print) -> Path:
    """변경안을 만들어 recordings/<id>/edits/<시각>.json으로 저장한다."""
    from runner.macro import run_macro
    rec_dir = RECORDINGS_DIR / rid
    macro_path = MACROS_DIR / f"{rid}.json"
    if not macro_path.exists():
        raise FileNotFoundError("매크로가 없습니다. 해석 화면에서 먼저 매크로를 만드세요.")
    macro = read_json(macro_path)
    man_path = MANUALS_DIR / f"{rid}.md"
    manual = fix_josa(man_path.read_text(encoding="utf-8")) if man_path.exists() else ""
    blocks = split_blocks(manual) if manual else []
    values = values or {}
    pid = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = {"id": pid, "rid": rid, "request": request, "created_at": now_iso(), "kind": macro.get("kind", "web"),
           "feasible": "unclear", "summary": "", "reason": "", "alternative": "",
           "macro_changes": [], "web_changes": [], "manual_changes": [], "problems": [], "warnings": [],
           "test": {"ran": False}, "applicable": False}

    log("AI에 변경안 요청")
    raw = _ai_propose(macro.get("name") or rid, macro, blocks, request)
    out["feasible"] = raw.get("feasible") if raw.get("feasible") in ("yes", "needs_recording", "unclear") else "unclear"
    for k in ("summary", "reason", "alternative"):
        out[k] = fix_josa(str(raw.get(k) or ""))
    log(f"판단: {out['feasible']} — {out['summary'] or out['reason']}")
    if out["feasible"] != "yes":
        return _save(rec_dir, out)

    new = copy.deepcopy(macro)
    by_no = {s["step"]: s for s in new["steps"]}
    heads = sheet_headers(new)
    problems: list[str] = []

    # 매크로 항목 바꾸기
    for ch in raw.get("macro_changes", []) or []:
        if not isinstance(ch, dict):
            continue
        step, field = by_no.get(ch.get("step")), str(ch.get("field", ""))
        if step is None:
            problems.append(f"없는 단계 번호: {ch.get('step')}")
            continue
        if field not in FIELDS:
            problems.append(f"{step['step']}단계의 '{field}'는 말로 바꿀 수 없는 항목입니다.")
            continue
        r = apply_field(new, step, field, ch.get("value"), heads, problems)
        if r and str(r[0]) != str(r[1]):   # 전과 후가 같으면 바뀐 것이 아님
            out["macro_changes"].append({"step": step["step"], "field": field, "before": r[0], "after": r[1]})

    # 안전 검사: 설명만 바뀌고 실제 실행 값은 그대로인 단계 (매뉴얼과 자동 실행이 달라짐)
    changed = {(c["step"], c["field"]) for c in out["macro_changes"]}
    for s in new["steps"]:
        old = next((x for x in macro["steps"] if x["step"] == s["step"]), None)
        if not old or (s["step"], "description") not in changed:
            continue
        if any((s["step"], f) in changed for f in ("value", "excel.cond.values", "excel.formula", "excel.header",
                                                     "excel.scope.values", "excel.scope.header")):
            continue
        old_val = _shown_value(macro, old)
        if old_val and len(old_val) >= 2 and old_val in old.get("description", "") and old_val not in s["description"]:
            problems.append(f"{s['step']}단계는 설명만 바뀌고 실제 실행 값('{old_val}')은 그대로입니다. "
                            "자동 실행과 매뉴얼이 달라지므로, 값도 바꾸도록 요청을 다시 적어 주세요.")

    # 웹: 같은 화면의 다른 항목 (화면 구조를 직접 읽어 고름)
    web_changes = [c for c in raw.get("web_element_changes", []) or [] if isinstance(c, dict)]
    if web_changes and new.get("kind") == "excel":
        problems.append("엑셀 매크로에는 화면 항목 바꾸기를 쓸 수 없습니다.")
        web_changes = []
    for ch in web_changes:
        step = by_no.get(ch.get("step"))
        if step is None:
            problems.append(f"없는 단계 번호: {ch.get('step')}")
            continue
        try:
            log(f"{step['step']}단계 화면 구조 읽는 중 (숨김 브라우저)")
            vals = _test_values(rid, macro, values)
            elements, url = inspect_elements(macro_path, vals, step["step"])
            log(f"  화면 항목 {len(elements)}개 → AI가 '{ch.get('target_hint', '')}' 찾는 중")
            pick = pick_element(request, str(ch.get("target_hint", "")), step, elements)
        except Exception as e:
            problems.append(str(e).splitlines()[0][:220])
            continue
        idx = pick.get("index")
        if not isinstance(idx, int) or not 0 <= idx < len(elements):
            problems.append(f"{step['step']}단계: 화면에서 '{ch.get('target_hint', '')}'에 맞는 항목을 찾지 못했습니다.")
            continue
        el = elements[idx]
        action = pick.get("action") if pick.get("action") in ("클릭", "입력", "선택") else step["action"]
        before = {"action": step["action"], "target": step["target"], "value": _shown_value(new, step)}
        step["locator"] = {"tag": el["tag"], "id": el.get("id", ""), "name": el.get("name", ""),
                           "input_type": el.get("type", ""), "label": el.get("label", ""), "group_label": "",
                           "text": el.get("text", "") if el["tag"] in ("a", "button") or el.get("type") in ("submit", "button") else "",
                           "selector": el.get("selector", "")}
        step["action"] = action
        step["target"] = fix_josa(str(pick.get("target") or el.get("label") or el.get("text") or step["target"]))
        val = str(ch.get("value") or pick.get("value") or "")
        if action in ("입력", "선택") and val:
            _set_value(new, step, val)
        elif action == "클릭":
            step["value"] = ""
        verb = {"클릭": "클릭한다", "입력": "입력한다", "선택": "선택한다"}[action]
        step["description"] = fix_josa(f"'{step['target']}'을(를) {verb}")
        out["web_changes"].append({"step": step["step"], "hint": ch.get("target_hint", ""), "reason": fix_josa(str(pick.get("reason", ""))),
                                   "before": before, "after": {"action": action, "target": step["target"], "value": _shown_value(new, step)},
                                   "element": {k: el.get(k) for k in ("tag", "id", "label", "text")}})

    # 매뉴얼 바꾸기: AI가 준 조각 + 설명이 바뀐 단계는 굵은 설명 자동 반영 (이미지 줄은 원래대로 유지)
    new_blocks = list(blocks)
    touched = set()
    for ch in raw.get("manual_changes", []) or []:
        if not isinstance(ch, dict) or not isinstance(ch.get("block"), int) or not 0 <= ch["block"] < len(blocks):
            continue
        i, text = ch["block"], fix_josa(str(ch.get("text", ""))).rstrip()
        if not text:
            continue
        imgs = [l for l in blocks[i].splitlines() if IMG_LINE.match(l)]
        new_blocks[i] = text + ("\n\n" + "\n\n".join(imgs) if imgs and "![" not in text else "") + "\n\n"
        touched.add(i)
    for s in new["steps"]:
        old = next((x for x in macro["steps"] if x["step"] == s["step"]), None)
        if not old:
            continue
        for i, b in enumerate(new_blocks):
            if i in touched or not re.match(rf"^{s['step']}\.\s", b):
                continue
            nb = b
            if s.get("description") != old.get("description"):
                nb = re.sub(r"^(\d+\.\s+\*\*)(.*?)(\*\*)", lambda m: m.group(1) + s["description"] + m.group(3), nb, count=1)
            if _shown_value(new, s) != _shown_value(macro, old) and not VAR.search(str(s.get("value", ""))):
                nb = re.sub(r"(값: `)([^`]*)(`)", lambda m: m.group(1) + _shown_value(new, s) + m.group(3), nb, count=1)
            if nb != b:
                new_blocks[i] = nb
                touched.add(i)
    for i in sorted(touched):
        strip = lambda t: "\n".join(l for l in t.splitlines() if not IMG_LINE.match(l)).strip()
        if strip(blocks[i]) != strip(new_blocks[i]):
            out["manual_changes"].append({"block": i, "before": strip(blocks[i]), "after": strip(new_blocks[i])})
    new_manual = "".join(new_blocks) if blocks else ""

    if not out["macro_changes"] and not out["web_changes"] and not out["manual_changes"] and not problems:
        problems.append("바뀌는 내용이 없습니다. 요청을 조금 더 구체적으로 적어 주세요.")
    out["problems"] = problems

    # 고친 매뉴얼 코드 검사 (빠진 단계 등)
    if new_manual:
        from .review import code_review
        out["warnings"] = [f"[{i['level']}] {i['message']}" for i in code_review(manual, new_manual, new)]

    # 시험 실행 (매크로가 바뀐 경우)
    edits_dir = rec_dir / "edits"
    edits_dir.mkdir(parents=True, exist_ok=True)
    new_path = edits_dir / f"{pid}_macro.json"
    write_json(new_path, new)
    # 실행에 영향을 주는 변경이 있을 때만 시험 실행 (설명·이름만 바뀌면 실행은 그대로)
    behavior = out["web_changes"] or any(c["field"] not in ("description", "target") for c in out["macro_changes"])
    if not problems and behavior:
        try:
            vals = _test_values(rid, new, values)
            first_web = min((c["step"] for c in out["web_changes"]), default=None)
            for attempt in range(MAX_ADAPT + 1):
                log("바꾼 매크로 시험 실행 중 (숨김)" if attempt == 0 else f"이어지는 단계를 맞춘 뒤 다시 시험 실행 ({attempt})")
                res = run_macro(new_path, vals, headless=True, checks=True, allow_changes=False, log=lambda *a: None)
                ok = res["success"] or res.get("stop_reason") == "user_declined"
                k = res.get("stopped_at")
                # 바꾼 화면 항목 때문에 다음 화면이 달라진 경우: 멈춘 단계를 새 화면에 맞춰 고치고 다시 시험
                if (ok or first_web is None or k is None or k <= first_web or attempt == MAX_ADAPT
                        or res.get("stop_reason") not in ("unexpected_page", "element_not_found")):
                    break
                if not _adapt_step(rid, request, new, new_path, vals, k, out, log):
                    break
            note = ("모든 단계 성공" if res["success"] else
                    f"데이터 변경 단계({res['stopped_at']}단계) 직전까지 성공 — 시험이라 저장·등록은 하지 않음" if ok else
                    f"{res['stopped_at']}단계에서 멈춤: {res.get('stop_message', '')}")
            out["test"] = {"ran": True, "success": ok, "message": note, "run_dir": res["run_dir"],
                           "downloads": res.get("downloads", []),
                           "steps": [{k: s.get(k) for k in ("step", "action", "target", "status", "message")} for s in res["steps"]]}
            log(f"시험 실행: {note}")
        except Exception as e:
            out["test"] = {"ran": True, "success": False, "message": f"시험 실행을 하지 못했습니다: {str(e).splitlines()[0][:200]}"}
            log(out["test"]["message"])
        # 이어서 맞춘 단계가 있으면 매뉴얼의 그 단계 설명도 반영
        _patch_manual_for_steps(blocks, new_blocks, macro, new, out, touched)
        new_manual = "".join(new_blocks) if blocks else ""
        if new_manual:   # 이어서 맞춘 뒤의 매뉴얼로 다시 검사
            from .review import code_review
            out["warnings"] = [f"[{i['level']}] {i['message']}" for i in code_review(manual, new_manual, new)]
    elif not problems:
        out["test"] = {"ran": False, "success": True, "message": "설명·매뉴얼만 바뀌고 실행 방식은 그대로라 시험 실행은 하지 않습니다."}

    out["applicable"] = not problems and out["test"].get("success", False)
    out["new_macro_file"] = new_path.name
    if new_manual:
        (edits_dir / f"{pid}_manual.md").write_text(new_manual, encoding="utf-8")
        out["new_manual_file"] = f"{pid}_manual.md"
    return _save(rec_dir, out)


MAX_ADAPT = 6   # 바뀐 화면에 맞춰 이어지는 단계를 고치는 최대 횟수


def _adapt_step(rid: str, request: str, new: dict, new_path: Path, vals: dict, k: int, out: dict, log) -> bool:
    """k단계가 새 화면에서 맞지 않음 → 그 화면 구조를 읽고, 원래 단계와 같은 역할의 항목으로 바꾼다."""
    step = next((s for s in new["steps"] if s["step"] == k), None)
    if step is None:
        return False
    try:
        log(f"{k}단계: 바뀐 화면에서 '{step['target']}' 역할의 항목 찾는 중 (숨김 브라우저)")
        elements, url = inspect_elements(new_path, vals, k)
        hint = (f"원래는 '{step['target']}'({step['action']}) 단계였다. 화면이 바뀌었으니, 이 새 화면에서 같은 역할을 하는 항목을 고른다 "
                f"(예: 조회 버튼 → 이 화면의 조회 버튼, 엑셀 다운로드 → 이 화면의 다운로드).")
        pick = pick_element(request, hint, step, elements)
    except Exception as e:
        out["problems"].append(f"{k}단계를 새 화면에 맞추지 못했습니다: {str(e).splitlines()[0][:150]}")
        return False
    idx = pick.get("index")
    if not isinstance(idx, int) or not 0 <= idx < len(elements):
        out["problems"].append(f"{k}단계 '{step['target']}'에 해당하는 항목이 바뀐 화면에 없습니다. 이 부분은 새로 녹화해야 합니다.")
        return False
    el = elements[idx]
    before = {"action": step["action"], "target": step["target"], "value": _shown_value(new, step)}
    step["locator"] = {"tag": el["tag"], "id": el.get("id", ""), "name": el.get("name", ""),
                       "input_type": el.get("type", ""), "label": el.get("label", ""), "group_label": "",
                       "text": el.get("text", "") if el["tag"] in ("a", "button") or el.get("type") in ("submit", "button") else "",
                       "selector": el.get("selector", "")}
    step["page_url"] = url   # 이 단계는 이제 새 화면에서 한다
    step["target"] = fix_josa(str(pick.get("target") or el.get("label") or el.get("text") or step["target"]))[:40]
    if step["action"] not in ("다운로드",):   # 다운로드는 다운로드 단계로 유지
        step["action"] = pick.get("action") if pick.get("action") in ("클릭", "입력", "선택") else step["action"]
    verb = {"클릭": "클릭한다", "입력": "입력한다", "선택": "선택한다", "다운로드": "눌러 파일을 내려받는다"}.get(step["action"], "클릭한다")
    step["description"] = fix_josa(f"'{step['target']}'을(를) {verb}")
    write_json(new_path, new)
    out["web_changes"].append({"step": k, "hint": "바뀐 화면에 맞춰 이어서 맞춤", "reason": fix_josa(str(pick.get("reason", ""))),
                               "before": before, "after": {"action": step["action"], "target": step["target"],
                                                           "value": _shown_value(new, step)},
                               "element": {kk: el.get(kk) for kk in ("tag", "id", "label", "text")}, "adapted": True})
    return True


def _patch_manual_for_steps(blocks: list[str], new_blocks: list[str], macro: dict, new: dict, out: dict, touched: set) -> None:
    """이어서 맞춘 단계의 설명을 매뉴얼 해당 단계에 반영하고, 매뉴얼 변경 목록을 다시 만든다."""
    for s in new["steps"]:
        old = next((x for x in macro["steps"] if x["step"] == s["step"]), None)
        if not old or s.get("description") == old.get("description"):
            continue
        for i, b in enumerate(new_blocks):
            if re.match(rf"^{s['step']}\.\s", b):
                nb = re.sub(r"^(\d+\.\s+\*\*)(.*?)(\*\*)(\s*\([^)]*\))?",
                            lambda m: m.group(1) + s["description"] + m.group(3) + f" ({s['action']} · {s['target']})", b, count=1)
                if nb != b:
                    new_blocks[i] = nb
                    touched.add(i)
    strip = lambda t: "\n".join(l for l in t.splitlines() if not IMG_LINE.match(l)).strip()
    out["manual_changes"] = [{"block": i, "before": strip(blocks[i]), "after": strip(new_blocks[i])}
                             for i in sorted(touched) if i < len(blocks) and strip(blocks[i]) != strip(new_blocks[i])]


def _save(rec_dir: Path, out: dict) -> Path:
    path = write_json(rec_dir / "edits" / f"{out['id']}.json", out)
    for p in out.get("problems", []):
        print(f"  ⚠ {p}")
    return path


# ------------------------------------------------------------------ 적용

def apply(rid: str, pid: str) -> dict:
    """변경안을 실제 매크로·단계·매뉴얼에 반영한다 (이전 버전 보관)."""
    from .manual import backup_manual
    edits = RECORDINGS_DIR / rid / "edits"
    prop = read_json(edits / f"{pid}.json")
    if not prop.get("applicable"):
        raise ValueError("이 변경안은 적용할 수 없습니다 (문제가 있거나 시험 실행에 실패).")
    if prop.get("applied_at"):
        raise ValueError("이미 적용한 변경안입니다.")
    new_macro = read_json(edits / prop["new_macro_file"])
    macro_path = MACROS_DIR / f"{rid}.json"
    hist = RECORDINGS_DIR / rid / "macro_history"
    hist.mkdir(exist_ok=True)
    if macro_path.exists():
        (hist / f"{datetime.now():%Y%m%d_%H%M%S}.json").write_text(macro_path.read_text(encoding="utf-8"), encoding="utf-8")
    new_macro["edited_at"] = now_iso()
    new_macro.setdefault("edits", []).append({"at": now_iso(), "request": prop["request"], "summary": prop["summary"]})
    write_json(macro_path, new_macro)

    # 단계 해석(steps.json)도 맞춘다 — 매뉴얼을 다시 만들 때 바뀐 내용이 유지되도록
    sp = RECORDINGS_DIR / rid / "steps.json"
    if sp.exists():
        steps = read_json(sp)
        mac = {s["step"]: s for s in new_macro["steps"]}
        for s in steps["steps"]:
            m = mac.get(s["step"])
            if not m:
                continue
            for k in ("action", "target", "description", "locator", "excel"):
                if k in m:
                    s[k] = m[k]
            s["value"] = _shown_value(new_macro, m)
        write_json(sp, steps)

    if prop.get("new_manual_file") and prop.get("manual_changes"):
        backup_manual(rid, "말로 고치기 전")
        (MANUALS_DIR / f"{rid}.md").write_text((edits / prop["new_manual_file"]).read_text(encoding="utf-8"), encoding="utf-8")
        write_json(RECORDINGS_DIR / rid / "manual_edited.json", {"edited_at": now_iso(), "by": "말로 고치기"})
    prop["applied_at"] = now_iso()
    write_json(edits / f"{pid}.json", prop)
    return prop


def main() -> None:
    parser = argparse.ArgumentParser(description="말로 고치기: 변경안 만들기")
    parser.add_argument("recording")
    parser.add_argument("--request-file", required=True)
    parser.add_argument("--values")
    args = parser.parse_args()
    rp = Path(args.request_file)
    request = json.loads(rp.read_text(encoding="utf-8"))["request"]
    rp.unlink(missing_ok=True)
    values = {}
    if args.values:
        vp = Path(args.values)
        values = json.loads(vp.read_text(encoding="utf-8"))
        vp.unlink(missing_ok=True)   # 비밀번호가 있을 수 있으므로 바로 지움
    try:
        path = propose(args.recording, request, values)
    except Exception as e:
        print(f"오류: {e}")
        sys.exit(1)
    print(f"RESULT_JSON={path}")


if __name__ == "__main__":
    main()
