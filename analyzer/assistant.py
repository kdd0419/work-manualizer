"""매뉴얼 묻고 답하기 (AI 기능 2): 매뉴얼의 특정 행동을 왜 하는지, 매뉴얼에 없는 관련 질문 등에 답한다.

근거: 매뉴얼 본문 + 녹화 단계 + 업무·화면 개요 + 담당자의 판단 기준 답변 + 이전 대화.
답마다 근거 수준을 붙인다: 근거 있음 / 일부 추정 / 추정 / 일반 지식 (추측을 사실처럼 말하지 않게).
대화는 recordings/<id>/chat.json에 남고, 좋은 답은 매뉴얼 '판단 기준'에 넣을 수 있다.
"""
import re
from datetime import datetime

from .common import MANUALS_DIR, RECORDINGS_DIR, fill_prompt, load_prompt, read_json, write_json
from .josa import fix_josa
from .review import strip_images

BASIS = ("근거 있음", "일부 추정", "추정", "일반 지식")
MAX_HISTORY = 6


def chat_path(rid: str):
    return RECORDINGS_DIR / rid / "chat.json"


def load_chat(rid: str) -> list[dict]:
    p = chat_path(rid)
    return read_json(p) if p.exists() else []


def ask(rid: str, question: str) -> dict:
    """질문에 답하고 대화 기록에 남긴다."""
    from .llm_client import INTERACTIVE_TIMEOUT, generate_json, last_model
    d = RECORDINGS_DIR / rid
    steps = read_json(d / "steps.json") if (d / "steps.json").exists() else {"steps": [], "recording_name": rid}
    ov = read_json(d / "overview.json") if (d / "overview.json").exists() else None
    answers = read_json(d / "answers.json").get("answers", []) if (d / "answers.json").exists() else []
    mp = MANUALS_DIR / f"{rid}.md"
    manual = fix_josa(mp.read_text(encoding="utf-8")) if mp.exists() else "(매뉴얼 없음)"
    history = load_chat(rid)[-MAX_HISTORY:]

    view_steps = [{"step": s["step"], "action": s["action"], "target": s["target"], "value": s.get("value", ""),
                   "description": fix_josa(s["description"]), "data_change": s.get("changes_data", False)}
                  for s in steps["steps"]]
    view_ov = {"task": ov["task"], "screens": [{"name": s["name"], "steps": s["steps"], "description": s["description"]}
                                                for s in ov["screens"]]} if ov else "(개요 없음)"
    prompt = fill_prompt(load_prompt("ask"), RECORDING_NAME=steps.get("recording_name", rid), OVERVIEW=view_ov,
                         STEPS=view_steps,
                         ANSWERS=[{"질문": a["question"], "답": a["answer"]} for a in answers if a.get("answer")] or "(없음)",
                         MANUAL=strip_images(manual)[:14000],
                         HISTORY=[{"질문": h["question"], "답": h["answer"]} for h in history] or "(없음)",
                         QUESTION=question)
    res = generate_json(prompt, timeout=INTERACTIVE_TIMEOUT + 10)   # 답은 검토보다 길어 조금 더 기다림
    res = res if isinstance(res, dict) else {}
    item = {
        "model": last_model(),
        "question": question,
        "answer": fix_josa(str(res.get("answer") or "답을 만들지 못했습니다. 질문을 조금 바꿔 다시 물어봐 주세요.")),
        "basis": res.get("basis") if res.get("basis") in BASIS else "추정",
        "refs": [str(r) for r in res.get("refs", []) if str(r).strip()][:6] if isinstance(res.get("refs"), list) else [],
        "add_to_manual": fix_josa(str(res.get("add_to_manual") or "")).strip(),
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    write_json(chat_path(rid), load_chat(rid) + [item])
    return item


def clear_chat(rid: str) -> None:
    chat_path(rid).unlink(missing_ok=True)


def add_criteria(rid: str, text: str) -> str:
    """매뉴얼 '## 판단 기준' 섹션에 한 줄 추가 (섹션이 없으면 '주의사항' 앞에 만든다). 바뀐 매뉴얼을 돌려준다."""
    from .manual import backup_manual
    p = MANUALS_DIR / f"{rid}.md"
    md = fix_josa(p.read_text(encoding="utf-8"))
    line = f"- {text.strip()}"
    m = re.search(r"^## 판단 기준\s*\n", md, re.M)
    if m:
        # 섹션 끝(다음 ## 직전)에 붙인다. 빈 목록 표시 '- (없음)'은 지운다
        nxt = re.search(r"^## ", md[m.end():], re.M)
        end = m.end() + (nxt.start() if nxt else len(md) - m.end())
        body = md[m.end():end].replace("- (없음)\n", "").rstrip("\n")
        md = md[:m.end()] + (body + "\n" if body else "") + line + "\n\n" + md[end:]
    else:
        m2 = re.search(r"^## 주의사항", md, re.M)
        block = f"## 판단 기준\n{line}\n\n"
        md = md[:m2.start()] + block + md[m2.start():] if m2 else md.rstrip("\n") + "\n\n" + block
    backup_manual(rid, "판단 기준 추가 전")
    p.write_text(md, encoding="utf-8")
    return md
