"""한국어 조사 자동 선택: '로그인 버튼을(를)' → '로그인 버튼을', '담당자이(가)' → '담당자가'.

규칙 기반으로 만든 문장은 앞말에 받침이 있는지 몰라 '을(를)'처럼 두 가지를 함께 적었다.
fix_josa()가 문장 속의 이런 표기를 찾아 앞 글자의 받침을 보고 하나로 고친다.
AI가 쓴 문장에는 보통 이런 표기가 없지만, 규칙 기반 값이 섞일 수 있으므로 항상 거친다.

    >>> fix_josa("'로그인 버튼'을(를) 클릭한다")
    "'로그인 버튼'을 클릭한다"
    >>> fix_josa("'점검결과'이(가) '적합'인 행")
    "'점검결과'가 '적합'인 행"
    >>> fix_josa("'부적합'(으)로 필터링")
    "'부적합'으로 필터링"
"""
import re

# 조사 표기 → (받침 있을 때, 받침 없을 때)
PAIRS = {
    "을(를)": ("을", "를"), "를(을)": ("을", "를"),
    "이(가)": ("이", "가"), "가(이)": ("이", "가"),
    "은(는)": ("은", "는"), "는(은)": ("은", "는"),
    "과(와)": ("과", "와"), "와(과)": ("과", "와"),
    "이에요(예요)": ("이에요", "예요"),
    "(이)다": ("이다", "다"),
}
RO = "(으)로"

# 앞말 끝에서 건너뛸 문자 (따옴표, 괄호 닫기, 공백 등): '로그인 버튼'을(를) → '튼'을 본다
_SKIP = set(" \t'\"’”」』)]}>`*…")

# 숫자를 읽었을 때 받침: 0(영) 1(일) 3(삼) 6(육) 7(칠) 8(팔) 10(십) 은 받침 있음, 그중 1·7·8은 ㄹ
_DIGIT_JONG = {"0": True, "1": True, "2": False, "3": True, "4": False,
               "5": False, "6": True, "7": True, "8": True, "9": False}
_DIGIT_RIEUL = {"1", "7", "8"}

# 영어 끝 글자: 우리말로 읽을 때 받침이 남는 경우 (l·r은 ㄹ)
_EN_JONG = set("lmnrLMNR")
_EN_RIEUL = set("lrLR")

_PATTERN = re.compile("|".join(re.escape(k) for k in sorted(list(PAIRS) + [RO], key=len, reverse=True)))


def _last_char(text: str) -> str:
    """앞말의 마지막 '읽히는' 글자. 괄호 설명은 건너뛴다: '파일(a.csv)' → '일'."""
    i = len(text) - 1
    while i >= 0:
        ch = text[i]
        if ch == ")":                       # 짝이 되는 '(' 앞으로 건너뜀
            depth, j = 0, i
            while j >= 0:
                depth += {")": 1, "(": -1}.get(text[j], 0)
                if depth == 0:
                    break
                j -= 1
            if j > 0 and text[j - 1] not in _SKIP:
                i = j - 1
                continue
        if ch not in _SKIP:
            return ch
        i -= 1
    return ""


def jong(ch: str) -> tuple[bool | None, bool]:
    """(받침이 있는지, 그 받침이 ㄹ인지). 알 수 없으면 (None, False)."""
    if not ch:
        return None, False
    code = ord(ch)
    if 0xAC00 <= code <= 0xD7A3:          # 한글 음절
        j = (code - 0xAC00) % 28
        return j != 0, j == 8
    if ch.isdigit():
        return _DIGIT_JONG.get(ch), ch in _DIGIT_RIEUL
    if ch.isascii() and ch.isalpha():
        return ch in _EN_JONG, ch in _EN_RIEUL
    return None, False


def pick(word: str, marker: str) -> str:
    """앞말 word 뒤에 붙일 조사 하나를 고른다. 판단할 수 없으면 marker 그대로."""
    has, rieul = jong(_last_char(word))
    if has is None:
        return marker
    if marker == RO:
        return "로" if (not has or rieul) else "으로"
    with_jong, without = PAIRS[marker]
    return with_jong if has else without


def fix_josa(text):
    """문장 속 '을(를)', '이(가)', '(으)로' 같은 표기를 앞말에 맞는 조사 하나로 바꾼다."""
    if not isinstance(text, str) or "(" not in text:
        return text
    out, pos = [], 0
    for m in _PATTERN.finditer(text):
        before = text[:m.start()]
        out.append(text[pos:m.start()])
        out.append(pick(before, m.group(0)))
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def josa(word: str, marker: str) -> str:
    """word + 알맞은 조사. 예) josa('버튼', '을(를)') → '버튼을'"""
    return f"{word}{pick(word, marker)}"
