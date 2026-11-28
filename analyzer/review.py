"""매뉴얼 수정 검토 (AI 기능 3): 고친 매뉴얼에 누락·오류가 생길 위험이 있는지 알려 준다.

두 겹으로 본다.
  1) 코드 검사 (AI 없이도 항상): 단계 번호 빠짐·중복, 지워진 단계(매크로에는 그대로 있음), 사라진 섹션,
     빠진 스크린샷, 없어진 '데이터 변경 단계' 표시, 비어 버린 매뉴얼
  2) AI 검사 (AI를 켰을 때): 수정 전·후와 실제 매크로 단계를 비교해 내용 모순·순서 오류·빠진 주의사항

결과: [{"level": "위험"|"주의"|"참고", "where": "...", "message": "...", "by": "코드"|"AI"}]
"""
import re

from .common import MACROS_DIR, MANUALS_DIR, fill_prompt, load_prompt, read_json
from .josa import fix_josa

STEP_LINE = re.compile(r"^(\d+)\.\s+(.*)$", re.M)
IMG = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
HEAD = re.compile(r"^## (.+)$", re.M)


def _steps(md: str) -> dict[int, str]:
    """맨 앞에 '번호. '로 시작하는 줄 → {번호: 내용}. 같은 번호가 여러 번이면 마지막."""
    return {int(n): t.strip() for n, t in STEP_LINE.findall(md)}


def _step_numbers(md: str) -> list[int]:
    return [int(n) for n, _ in STEP_LINE.findall(md)]


def _block(md: str, n: int) -> str:
    """n단계 줄부터 다음 단계·섹션 직전까지의 글."""
    m = re.search(rf"^{n}\.\s.*?(?=^\d+\.\s|^## |\Z)", md, re.M | re.S)
    return m.group(0) if m else ""


def _plain(text: str) -> str:
    return re.sub(r"[*`]", "", text).strip()


def strip_images(md: str) -> str:
    """AI에 보낼 때 이미지 줄은 '[스크린샷]'으로 줄인다."""
    return IMG.sub("[스크린샷]", md)


def code_review(old: str, new: str, macro: dict | None) -> list[dict]:
    issues: list[dict] = []

    def add(level, where, msg):
        issues.append({"level": level, "where": where, "message": msg, "by": "코드"})

    if not new.strip():
        add("위험", "전체", "매뉴얼이 비어 있습니다.")
        return issues
    if not re.search(r"^# ", new, re.M):
        add("주의", "제목", "매뉴얼 제목(# …)이 없어졌습니다.")

    old_heads, new_heads = HEAD.findall(old), HEAD.findall(new)
    for h in old_heads:
        if h not in new_heads:
            add("주의", f"'{h}' 섹션", f"'{h}' 섹션이 통째로 없어졌습니다.")

    nums = _step_numbers(new)
    dup = sorted({n for n in nums if nums.count(n) > 1})
    if dup:
        add("주의", "처리 순서", f"단계 번호가 겹칩니다: {', '.join(map(str, dup))}단계.")
    if nums:
        expect = set(range(1, max(nums) + 1))
        gap = sorted(expect - set(nums))
        if gap:
            add("주의", "처리 순서", f"단계 번호가 건너뜁니다: {', '.join(map(str, gap))}단계가 없습니다.")

    old_steps, new_steps = _steps(old), _steps(new)
    mac = {s["step"]: s for s in (macro or {}).get("steps", [])}
    for n, text in old_steps.items():
        if n in new_steps:
            continue
        if n in mac:
            add("위험", f"{n}단계", fix_josa(f"{n}단계 '{_plain(text)[:40]}'이(가) 매뉴얼에서 빠졌습니다. 매크로에는 이 단계가 그대로 있어서, "
                                        "매뉴얼만 보고 하는 사람과 자동 실행이 서로 달라집니다."))
        else:
            add("주의", f"{n}단계", fix_josa(f"{n}단계 '{_plain(text)[:40]}'이(가) 빠졌습니다."))
    for n in new_steps:
        if "데이터 변경 단계" in _block(old, n) and "데이터 변경 단계" not in _block(new, n):
            add("위험", f"{n}단계", f"{n}단계의 '데이터 변경 단계' 표시가 없어졌습니다. 저장·등록처럼 되돌리기 어려운 단계임을 알려 주는 표시입니다.")

    lost_imgs = [p for p in IMG.findall(old) if p not in IMG.findall(new)]
    if lost_imgs:
        add("참고", "스크린샷", f"스크린샷 {len(lost_imgs)}장이 매뉴얼에서 빠집니다.")
    if len(new) < len(old) * 0.4 and len(old) > 400:
        add("주의", "전체", f"매뉴얼 분량이 고치기 전의 {round(len(new) / len(old) * 100)}%로 크게 줄었습니다.")
    return issues


AI_REVIEW_TIMEOUT = 45   # 초 (모델 한 번은 20초, 막힌 모델은 바로 건너뛰므로 보통 몇 초 안에 끝남)


def line_diff(old: str, new: str, limit: int = 6000) -> str:
    """바뀐 줄만 '- 지운 줄 / + 새 줄'로 (가벼운 모델도 무엇이 바뀌었는지 놓치지 않게 코드가 미리 계산)."""
    import difflib
    a, b = strip_images(old).splitlines(), strip_images(new).splitlines()
    out = [l for l in difflib.unified_diff(a, b, lineterm="", n=0)
           if l[:1] in "+-" and not l.startswith(("+++", "---")) and l[1:].strip()]
    return ("\n".join(out) or "(바뀐 줄 없음)")[:limit]


def ai_review(name: str, old: str, new: str, macro: dict | None, code_issues: list[dict] | None = None) -> list[dict]:
    from .llm_client import INTERACTIVE_TIMEOUT, generate_json
    steps = [{"step": s["step"], "action": s["action"], "target": s["target"], "value": s.get("value", ""),
              "changes_data": s.get("changes_data", False)} for s in (macro or {}).get("steps", [])]
    found = "\n".join(f"- [{i['level']}] {i['where']}: {i['message']}" for i in (code_issues or [])) or "(없음)"
    res = generate_json(fill_prompt(load_prompt("review", "v2"), RECORDING_NAME=name, MACRO_STEPS=steps or "(매크로 없음)",
                                    DIFF=line_diff(old, new), CODE_FOUND=found, NEW=strip_images(new)[:12000]),
                        timeout=INTERACTIVE_TIMEOUT)
    out = []
    for it in (res.get("issues", []) if isinstance(res, dict) else []):
        if isinstance(it, dict) and str(it.get("message", "")).strip():
            lvl = it.get("level") if it.get("level") in ("위험", "주의") else "주의"
            out.append({"level": lvl, "where": str(it.get("where", "")), "message": fix_josa(str(it["message"])), "by": "AI"})
    return out


def review(rid: str, new_md: str, use_ai: bool = True) -> dict:
    """지금 저장된 매뉴얼과 고친 매뉴얼을 비교 검토한다."""
    p = MANUALS_DIR / f"{rid}.md"
    old = fix_josa(p.read_text(encoding="utf-8")) if p.exists() else ""
    mp = MACROS_DIR / f"{rid}.json"
    macro = read_json(mp) if mp.exists() else None
    issues = code_review(old, new_md, macro)
    ai_used, ai_error, ai_model = False, "", ""
    if use_ai and old.strip() != new_md.strip():
        # AI가 혼잡하면 재시도·예비 모델 때문에 몇 분 걸릴 수 있어, 정해진 시간만 기다리고 코드 검사 결과로 진행한다
        import threading
        box: dict = {}

        def work():
            try:
                box["issues"] = ai_review((macro or {}).get("name") or rid, old, new_md, macro, list(issues))
                from .llm_client import last_model
                box["model"] = last_model()
            except Exception as e:
                box["error"] = str(e).splitlines()[0][:200]

        t = threading.Thread(target=work, daemon=True)
        t.start()
        t.join(AI_REVIEW_TIMEOUT)
        if "issues" in box:
            issues += box["issues"]
            ai_used, ai_model = True, box.get("model", "")
        else:
            ai_error = box.get("error") or f"AI 응답이 {AI_REVIEW_TIMEOUT}초 안에 오지 않아 코드 검사만 했습니다"
    order = {"위험": 0, "주의": 1, "참고": 2}
    issues.sort(key=lambda i: order.get(i["level"], 3))
    return {"issues": issues, "ai_used": ai_used, "ai_error": ai_error, "ai_model": ai_model}
