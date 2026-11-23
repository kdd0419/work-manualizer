"""v4 - 사람의 판단이 들어간 지점을 찾아 질문(2~5개)을 만들고 답변을 받는다.

실행:
  python -m analyzer.questions recordings/<id> [--no-ai]   # 질문 생성 → questions.json
  python -m analyzer.questions recordings/<id> --answer    # 터미널에서 답변 → answers.json
답변을 저장한 뒤 analyzer.manual을 다시 실행하면 매뉴얼에 "판단 기준"과 "예외 상황"이 추가된다.
"""
import argparse
from pathlib import Path

from .common import fill_prompt, load_prompt, now_iso, read_json, resolve_recording, write_json

MIN_Q, MAX_Q = 2, 5


def _rule_based(steps: list[dict]) -> list[dict]:
    """AI 없이 만드는 기본 질문."""
    qs = []
    for s in steps:
        if s["action"] == "선택":
            qs.append({"kind": "판단 기준", "question": f"'{s['target']}'에서 '{s['value']}'을(를) 고른 기준은 무엇인가요?",
                       "why": "선택값의 기준을 알아야 다른 상황에서도 같은 판단을 할 수 있음", "related_steps": [s["step"]]})
    if any(s["action"] == "다운로드" for s in steps):
        qs.append({"kind": "예외 상황", "question": "조회 결과가 없으면 어떻게 하나요?",
                   "why": "결과 0건일 때의 처리 방법", "related_steps": []})
    if any(s["changes_data"] for s in steps):
        qs.append({"kind": "예외 상황", "question": "저장한 내용이 잘못되었으면 어떻게 바로잡나요?",
                   "why": "데이터 변경 단계의 되돌리기 방법", "related_steps": []})
    qs.append({"kind": "판단 기준", "question": "이 업무는 언제(주기·시점) 하고, 결과물은 어디에 쓰나요?",
               "why": "업무 목적과 시점 확인", "related_steps": []})
    return qs


def make_questions(rec: str | Path, use_ai: bool = True, prompt_version: str = "v1", log=print) -> Path:
    """질문을 만들어 recordings/<id>/questions.json에 저장한다."""
    rec_dir = resolve_recording(rec)
    data = read_json(rec_dir / "steps.json")
    steps = data["steps"]
    if use_ai:
        from .llm_client import generate_json
        log("AI 질문 생성 요청")
        view = [{k: s[k] for k in ("step", "action", "target", "value", "description", "changes_data")
                 if k in s} for s in steps]
        res = generate_json(fill_prompt(load_prompt("questions", prompt_version),
                                        RECORDING_NAME=data.get("recording_name", ""), STEPS=view))
        raw = res.get("questions", []) if isinstance(res, dict) else []
    else:
        raw = _rule_based(steps)

    questions = []
    for q in raw:
        if isinstance(q, dict) and str(q.get("question", "")).strip():
            kind = q.get("kind") if q.get("kind") in ("판단 기준", "예외 상황") else "판단 기준"
            questions.append({"id": f"q{len(questions) + 1}", "kind": kind,
                              "question": q["question"].strip(), "why": str(q.get("why", "")),
                              "related_steps": [n for n in q.get("related_steps", []) if isinstance(n, int)]})
    if len(questions) < MIN_Q:   # 너무 적으면 기본 질문으로 보충
        for q in _rule_based(steps):
            if len(questions) >= MIN_Q:
                break
            questions.append({"id": f"q{len(questions) + 1}", **q})
    questions = questions[:MAX_Q]

    path = write_json(rec_dir / "questions.json",
                      {"recording_id": data["recording_id"], "generated_at": now_iso(),
                       "questions": questions})
    for q in questions:
        log(f"  {q['id']} [{q['kind']}] {q['question']}")
    log(f"질문 {len(questions)}개 → {path}")
    return path


def save_answers(rec: str | Path, answers: dict[str, str]) -> Path:
    """질문 id → 답변을 answers.json으로 저장한다."""
    rec_dir = resolve_recording(rec)
    qs = read_json(rec_dir / "questions.json")["questions"]
    items = [{"id": q["id"], "kind": q["kind"], "question": q["question"],
              "answer": answers.get(q["id"], "").strip()} for q in qs]
    return write_json(rec_dir / "answers.json", {"answered_at": now_iso(), "answers": items})


def load_qa(rec: str | Path) -> tuple[list[dict], dict[str, str]]:
    """질문 목록과 저장된 답변(dict)을 읽는다."""
    rec_dir = resolve_recording(rec)
    qpath, apath = rec_dir / "questions.json", rec_dir / "answers.json"
    qs = read_json(qpath)["questions"] if qpath.exists() else []
    ans = {a["id"]: a["answer"] for a in read_json(apath)["answers"]} if apath.exists() else {}
    return qs, ans


def answer_cli(rec: str | Path) -> None:
    """터미널에서 질문에 하나씩 답한다 (빈칸이면 기존 답 유지)."""
    qs, ans = load_qa(rec)
    if not qs:
        print("questions.json이 없습니다. 먼저 질문을 생성하세요.")
        return
    for q in qs:
        old = ans.get(q["id"], "")
        new = input(f"\n[{q['kind']}] {q['question']}\n{'(기존: ' + old + ') ' if old else ''}답변> ").strip()
        ans[q["id"]] = new or old
    print(f"답변 저장 → {save_answers(rec, ans)}")


def main() -> None:
    """명령줄 실행."""
    parser = argparse.ArgumentParser(description="판단 기준 질문 생성/답변")
    parser.add_argument("recording", help="녹화 폴더 (recordings/<id>)")
    parser.add_argument("--no-ai", action="store_true", help="AI 없이 기본 질문 생성 (테스트용)")
    parser.add_argument("--answer", action="store_true", help="터미널에서 답변 입력")
    parser.add_argument("--prompt-version", default="v1")
    args = parser.parse_args()
    if args.answer:
        answer_cli(args.recording)
    else:
        make_questions(args.recording, use_ai=not args.no_ai, prompt_version=args.prompt_version)


if __name__ == "__main__":
    main()
