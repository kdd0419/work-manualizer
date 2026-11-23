"""변수 기본값 규칙(today, today-1, month_start …) 계산과 날짜 형식 맞추기."""
import re
from datetime import date, timedelta

DATE_RULE = re.compile(r"today([+-]\d+)?")
RULES = ("today", "today-N", "today+N", "month_start", "prev_month_start", "prev_month_end",
         "recorded", "직접 입력")
_FORMATS = [  # (정규식, strftime 형식)
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "%Y-%m-%d"),
    (re.compile(r"^\d{8}$"), "%Y%m%d"),
    (re.compile(r"^\d{4}\.\d{2}\.\d{2}$"), "%Y.%m.%d"),
    (re.compile(r"^\d{4}/\d{2}/\d{2}$"), "%Y/%m/%d"),
]


def date_format_of(value: str) -> str | None:
    """값이 날짜처럼 생겼으면 그 형식(strftime)을, 아니면 None."""
    for pattern, fmt in _FORMATS:
        if pattern.match(str(value or "")):
            return fmt
    return None


def parse_date(value: str) -> date | None:
    """여러 형식의 날짜 문자열을 date로."""
    from datetime import datetime
    fmt = date_format_of(value)
    return datetime.strptime(value, fmt).date() if fmt else None


def is_valid_rule(rule: str) -> bool:
    """허용된 기본값 규칙인지."""
    return rule in RULES or bool(DATE_RULE.fullmatch(rule or ""))


def apply_rule(rule: str, recorded: str, today: date | None = None) -> str | None:
    """규칙으로 기본값을 계산한다. '직접 입력'이면 None(사람이 입력해야 함)."""
    today = today or date.today()
    rule = (rule or "").strip()
    fmt = date_format_of(recorded) or "%Y-%m-%d"
    m = DATE_RULE.fullmatch(rule)
    if m:
        return (today + timedelta(days=int(m.group(1) or 0))).strftime(fmt)
    if rule == "month_start":
        return today.replace(day=1).strftime(fmt)
    if rule == "prev_month_end":
        return (today.replace(day=1) - timedelta(days=1)).strftime(fmt)
    if rule == "prev_month_start":
        return (today.replace(day=1) - timedelta(days=1)).replace(day=1).strftime(fmt)
    if rule == "recorded":
        return recorded
    return None


def guess_rule(value: str, recorded_on: date) -> str | None:
    """녹화일 기준으로 날짜 값의 규칙을 추정한다 (AI 없이 쓰는 규칙 기반)."""
    d = parse_date(value)
    if d is None:
        return None
    if d == recorded_on.replace(day=1) and d != recorded_on:
        return "month_start"
    diff = (d - recorded_on).days
    return "today" if diff == 0 else f"today{diff:+d}"
