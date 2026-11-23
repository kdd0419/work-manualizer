"""매크로 실행 (v3: 변수 매크로, v4: + 실패 감지·데이터 변경 확인).

실행:
  python -m runner.macro macros/<id>.json --var 기준일자=2026-11-20
옵션:
  --var 이름=값      변수 값 지정 (여러 번 가능). 없으면 기본값 규칙으로 계산
  --yes              실행 전 y/n 확인 생략
  --allow-changes    저장·등록 같은 데이터 변경 단계를 묻지 않고 실행
  --no-checks        v3 방식(실패 감지·변경 확인 없이 실행). 평가 비교용
  --headless         브라우저 창 없이 실행
결과: runs/<id>_<타임스탬프>/result.json (v1과 같은 형식)
"""
import argparse
import getpass
import json
import re
import sys
from pathlib import Path
from typing import Callable

from playwright.sync_api import Page, sync_playwright

from .common import (HUMAN_HANDOFF, STOP_REASONS, VIEWPORT, detect_problem, fail_screenshot,
                     find_element, finish_result, new_result, new_run_dir, page_messages,
                     save_result, settle, url_path)
from .dates import apply_rule

VAR_PATTERN = re.compile(r"\{\{(.+?)\}\}")


class MacroError(RuntimeError):
    """실행 전 단계의 오류 (변수 누락 등)."""


class StopRun(Exception):
    """실행을 멈춰야 하는 상황. code는 STOP_REASONS의 키."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code, self.detail = code, detail


def load_macro(path: str | Path) -> dict:
    """매크로 JSON을 읽는다."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def resolve_variables(macro: dict, overrides: dict[str, str],
                      ask: Callable[[dict], str] | None = None) -> dict[str, str]:
    """변수 값 결정: 직접 지정 > 기본값 규칙 > (직접 입력이면) ask로 물어봄."""
    names = {v["name"] for v in macro["variables"]}
    unknown = set(overrides) - names
    if unknown:
        raise MacroError(f"매크로에 없는 변수입니다: {', '.join(sorted(unknown))} "
                         f"(사용 가능: {', '.join(sorted(names)) or '없음'})")
    values = {}
    for v in macro["variables"]:
        val = overrides.get(v["name"])
        if val is None:
            val = apply_rule(v["default_rule"], v["recorded_value"])
        if val is None:
            if ask is None:
                raise MacroError(f"'{v['name']}' 값을 직접 입력해야 합니다. --var {v['name']}=값")
            val = ask(v)
        values[v["name"]] = val
    return values


def render(value: str, values: dict[str, str]) -> str:
    """'{{변수명}}'을 실제 값으로 바꾼다."""
    return VAR_PATTERN.sub(lambda m: values[m.group(1)], value or "")


def _is_secret(name: str) -> bool:
    return "비밀번호" in name or "password" in name.lower()


def display_values(values: dict[str, str]) -> dict[str, str]:
    """화면·로그용: 비밀번호 변수는 가린다."""
    return {k: ("***" if _is_secret(k) else v) for k, v in values.items()}


def _select(page: Page, step: dict, el, value: str) -> None:
    """드롭다운 또는 라디오 버튼에서 값을 고른다."""
    loc = step["locator"]
    if loc.get("input_type") == "radio":
        radio = page.locator(f'input[type="radio"][name="{loc.get("name", "")}"][value="{value}"]')
        if radio.count() != 1:
            radio = page.get_by_label(value, exact=True)
        if radio.count() != 1:
            raise StopRun("element_not_found", f"라디오 선택지 '{value}'")
        radio.check()
        return
    for kwargs in ({"label": value}, {"value": value}):
        try:
            el.select_option(**kwargs, timeout=2000)
            return
        except Exception:
            pass
    # 일부만 맞는 선택지 (예: '샘플IT서비스' → '샘플IT서비스 (IT)')
    options = el.locator("option").all_inner_texts()
    matches = [o for o in options if value in o]
    if len(matches) == 1:
        el.select_option(label=matches[0])
        return
    raise StopRun("element_not_found", f"드롭다운 선택지 '{value}'")


def run_macro(macro_path: str | Path, values: dict[str, str], headless: bool = False,
              checks: bool = True, allow_changes: bool = False,
              confirm: Callable[[dict], bool] | None = None,
              log: Callable[[str], None] = print) -> dict:
    """매크로를 실행하고 결과 dict를 돌려준다.

    checks=True(v4): 페이지 확인, 0건·오류 메시지 감지, 데이터 변경 단계 확인
    confirm: 데이터 변경 단계 직전에 호출 (True면 실행). 없으면 allow_changes 값을 따른다.
    """
    macro = load_macro(macro_path)
    run_dir = new_run_dir(macro["id"])
    result = new_result("macro", str(macro_path), run_dir)
    result["version"] = "v4" if checks else "v3"
    result["variables"] = display_values(values)
    result["total"] = len(macro["steps"])

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(viewport=VIEWPORT, accept_downloads=True, locale="ko-KR")
        page = context.new_page()

        for step in macro["steps"]:
            n, action = step["step"], step["action"]
            value = render(step.get("value", ""), values)
            shown = "***" if any(_is_secret(m) for m in VAR_PATTERN.findall(step.get("value", ""))) else value
            rec = {"step": n, "action": action, "target": step["target"], "value": shown,
                   "status": "성공", "message": ""}
            try:
                if action == "이동":
                    page.goto(value or step["page_url"])
                else:
                    # v4: 녹화 때와 같은 페이지에 있는지 확인
                    if checks and step.get("page_url") and url_path(page.url) != url_path(step["page_url"]):
                        raise StopRun("unexpected_page",
                                      f"현재 {url_path(page.url)}, 예상 {url_path(step['page_url'])}")
                    # v4: 데이터 변경 단계는 실행 직전에 확인
                    if checks and step.get("changes_data"):
                        ok = confirm(step) if confirm else allow_changes
                        if not ok:
                            raise StopRun("user_declined", f"{n}단계({step['target']}) 실행 취소")
                    el = None
                    if not (action == "선택" and step["locator"].get("input_type") == "radio"):
                        el, how = find_element(page, step["locator"])
                        if el is None:
                            raise StopRun("element_not_found", step["target"])
                        if how != "id":
                            rec["message"] = f"({how}로 찾음) "
                    if action == "입력":
                        el.fill(value)
                    elif action == "선택":
                        _select(page, step, el, value)
                    elif action == "다운로드":
                        try:
                            with page.expect_download(timeout=10000) as info:
                                el.click()
                        except Exception:
                            raise StopRun("download_missing", step["target"])
                        dl = info.value
                        dl.save_as(str(run_dir / dl.suggested_filename))
                        result["downloads"].append(dl.suggested_filename)
                        rec["message"] += f"다운로드: {dl.suggested_filename}"
                    elif action == "제출":
                        el.evaluate("f => (f.form || f).requestSubmit()")
                    else:
                        el.click()
                settle(page)
                result["messages"] += [m for m in page_messages(page) if m not in result["messages"]]
                # v4: 조회 결과 0건, 오류 메시지 감지
                if checks and action in ("클릭", "제출"):
                    problem = detect_problem(page)
                    if problem:
                        raise StopRun(problem[0], problem[1])
            except StopRun as e:
                rec["status"] = "실패"
                rec["message"] += f"{STOP_REASONS[e.code]} [{e.detail}]"
                rec["reason"] = e.code
            except Exception as e:
                rec["status"] = "실패"
                rec["message"] += f"{STOP_REASONS['action_failed']} ({str(e).splitlines()[0]})"
                rec["reason"] = "action_failed"

            if rec["status"] == "실패":
                rec["screenshot"] = fail_screenshot(page, run_dir, n)
                result.update(stopped_at=n, stop_reason=rec["reason"],
                              stop_message=f"{rec['message']} {HUMAN_HANDOFF}")
            result["steps"].append(rec)
            log(f"[{n:02d}] {rec['status']:<2} {action:<4} {step['target']} {rec['message']}")
            if rec["status"] == "실패":
                log(f"\n⛔ {n}단계에서 멈췄습니다: {rec['message']}")
                if rec.get("screenshot"):
                    log(f"   화면: {run_dir / rec['screenshot']}")
                log(f"   {HUMAN_HANDOFF}")
                break
        browser.close()

    finish_result(result)
    save_result(run_dir, result)
    log(f"총 {result['total']}단계 중 {result['succeeded']}단계 성공 → {run_dir / 'result.json'}")
    return result


def describe(macro: dict, values: dict[str, str]) -> str:
    """실행 전 확인용: 변수 값과 단계 목록."""
    lines = [f"매크로: {macro.get('name') or macro['id']}", "", "[변수]"]
    shown = display_values(values)
    for v in macro["variables"]:
        lines.append(f"  {v['name']} = {shown[v['name']]!r}   (규칙: {v['default_rule']}, 녹화값: {v['recorded_value']!r})")
    if not macro["variables"]:
        lines.append("  (없음)")
    lines += ["", "[단계]"]
    for s in macro["steps"]:
        val = render(s.get("value", ""), shown)
        mark = "  ⚠ 데이터 변경" if s.get("changes_data") else ""
        lines.append(f"  {s['step']:>2}. [{s['action']}] {s['description']}"
                     + (f"  → {val!r}" if val and s["action"] in ("입력", "선택") else "") + mark)
    return "\n".join(lines)


def _ask_cli(v: dict) -> str:
    """터미널에서 '직접 입력' 변수 값을 묻는다."""
    prompt = f"{v['name']} ({v.get('description', '')}) 값을 입력하세요: "
    try:
        return getpass.getpass(prompt) if _is_secret(v["name"]) else input(prompt)
    except EOFError:
        raise MacroError(f"'{v['name']}' 값을 입력받을 수 없습니다. --var {v['name']}=값 으로 지정하세요.")


def _confirm_cli(step: dict) -> bool:
    """터미널에서 데이터 변경 단계 실행 여부를 묻는다."""
    try:
        ans = input(f"\n⚠ {step['step']}단계 '{step['description']}'는 데이터를 변경합니다. 실행할까요? (y/n): ")
    except EOFError:
        return False
    return ans.strip().lower() in ("y", "yes", "ㅛ")


def parse_vars(items: list[str]) -> dict[str, str]:
    """['이름=값', ...] → dict."""
    out = {}
    for item in items or []:
        if "=" not in item:
            raise MacroError(f"--var 형식이 잘못되었습니다: {item!r} (예: --var 기준일자=2026-11-20)")
        k, v = item.split("=", 1)
        out[k.strip()] = v
    return out


def main() -> None:
    """명령줄 실행."""
    parser = argparse.ArgumentParser(description="매크로 실행")
    parser.add_argument("macro", help="매크로 파일 (macros/<id>.json)")
    parser.add_argument("--var", action="append", default=[], help="변수 지정: 이름=값")
    parser.add_argument("--yes", action="store_true", help="실행 전 확인 생략")
    parser.add_argument("--allow-changes", action="store_true", help="데이터 변경 단계를 묻지 않고 실행")
    parser.add_argument("--no-checks", action="store_true", help="v3 방식: 실패 감지 없이 실행")
    parser.add_argument("--headless", action="store_true", help="브라우저 창 없이 실행")
    args = parser.parse_args()

    try:
        macro = load_macro(args.macro)
        values = resolve_variables(macro, parse_vars(args.var), ask=_ask_cli)
    except MacroError as e:
        print(f"오류: {e}")
        raise SystemExit(2)

    print(describe(macro, values))
    if not args.yes:
        try:
            if input("\n이대로 실행할까요? (y/n): ").strip().lower() not in ("y", "yes", "ㅛ"):
                print("취소했습니다.")
                raise SystemExit(1)
        except EOFError:
            print("확인 입력을 받을 수 없습니다. --yes 옵션을 쓰세요.")
            raise SystemExit(2)

    confirm = None if args.allow_changes or not sys.stdin.isatty() else _confirm_cli
    result = run_macro(args.macro, values, headless=args.headless, checks=not args.no_checks,
                       allow_changes=args.allow_changes, confirm=confirm)
    print(f"RESULT_JSON={Path(result['run_dir']) / 'result.json'}")
    raise SystemExit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
