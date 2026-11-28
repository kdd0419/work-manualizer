"""LLM 호출은 이 파일 한 곳에서만 한다.

두 가지 연결 방식을 지원한다 (.env의 LLM_PROVIDER로 선택, 코드 수정 없이 바꿈):
  gemini  Google Gemini API (google-genai SDK). 시험·개발용 무료 등급. ※ 외부 서비스
  openai  OpenAI 호환 API — 사내 AI 게이트웨이, 사내 설치 모델(vLLM·Ollama 등) 대부분이 이 형식을 쓴다.
          실제 업무 데이터(내부 정보)로 쓸 때는 이 방식으로 사내 API에 연결한다.

설정(.env):
  LLM_PROVIDER        gemini(기본) | openai
  LLM_API_KEY         API 키 (사내 API가 키 없이 열려 있으면 비워도 됨 — openai 방식만)
  LLM_MODEL           모델 이름 (gemini 기본: gemini-3.5-flash)
  LLM_FALLBACK_MODEL  기본 모델이 혼잡(503)할 때 쓸 예비 모델, 쉼표로 여러 개 (gemini 기본: gemini-3.8-flash,gemini-3.5-flash-lite)
  LLM_BASE_URL        openai 방식의 주소 (예: https://ai.사내도메인/v1) — /chat/completions 앞까지
  LLM_SEND_IMAGES     true(기본) | false — 이미지를 못 받는 모델이면 false (스크린샷 없이 글만 보냄)
  LLM_VERIFY_SSL      true(기본) | false — 사내 인증서 때문에 접속이 안 될 때만 false
  LLM_TIMEOUT         응답 대기 초 (기본 120)

연결 확인:
  python -m analyzer.llm_client          # 짧은 질문으로 응답 확인
  python -m analyzer.llm_client --list   # 이 키로 쓸 수 있는 모델 목록
"""
import argparse
import base64
import io
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# 2026-11 기준 새 키에서 쓸 수 있는 Gemini 무료 모델 (gemini-2.5-flash는 새 사용자 불가, 3.8-flash는 혼잡(503)이 잦음)
DEFAULT_MODEL = "gemini-3.5-flash"
DEFAULT_FALLBACK_MODEL = "gemini-3.8-flash,gemini-3.5-flash-lite"   # 쉼표로 여러 개, 앞에서부터 차례로
MAX_RETRIES = 4          # 남은 모델이 하나뿐일 때 일시 오류 재시도 횟수
IMAGE_MAX_WIDTH = 1024   # 비용·한도 절약을 위해 스크린샷을 이 폭으로 줄여 보낸다
RETRYABLE = (429, 500, 502, 503, 504)

# 막힌 모델은 잠시 쉬게 한다 (같은 모델에 몇 분씩 매달리지 않도록). 값: 쉬는 시간(초)
COOLDOWN_QUOTA = 15 * 60   # 무료 한도 초과
COOLDOWN_BUSY = 3 * 60     # 혼잡(503)·시간 초과·서버 오류
INTERACTIVE_TIMEOUT = 20   # 화면에서 바로 쓰는 기능(검토·질문)의 모델 한 번 대기 시간(초)
_cooldown: dict[str, float] = {}   # 모델 → 다시 써도 되는 시각
_last_model = ""                   # 마지막으로 답한 모델
# 관리 서버와 백그라운드 작업(해석·말로 고치기)이 서로 다른 프로세스라, 쉬는 모델 정보를 파일로 함께 쓴다
_COOLDOWN_FILE = Path(__file__).resolve().parent.parent / "runs" / ".llm_cooldown.json"


def _load_cooldown() -> None:
    try:
        data = json.loads(_COOLDOWN_FILE.read_text(encoding="utf-8"))
        now = time.time()
        for m, t in data.items():
            if float(t) > now:
                _cooldown[m] = max(_cooldown.get(m, 0), float(t))
    except Exception:
        pass


def _save_cooldown() -> None:
    try:
        now = time.time()
        _COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _COOLDOWN_FILE.write_text(json.dumps({m: t for m, t in _cooldown.items() if t > now}), encoding="utf-8")
    except Exception:
        pass


class LLMError(RuntimeError):
    """LLM 호출 실패 (키 없음, 한도 초과 지속, JSON 형식 위반 등)."""


class _HTTPError(Exception):
    """연결 방식과 상관없이 같은 모양으로 다루기 위한 오류 (code, message)."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _flag(name: str, default: bool) -> bool:
    v = os.getenv(name, "").strip().lower()
    return default if not v else v in ("1", "true", "yes", "y", "on")


def provider() -> str:
    """연결 방식: 'gemini' 또는 'openai'."""
    p = os.getenv("LLM_PROVIDER", "").strip().lower()
    return "openai" if p in ("openai", "openai_compatible", "internal", "사내") else "gemini"


def provider_label() -> str:
    """화면 표시용 이름."""
    return "사내 AI(OpenAI 호환)" if provider() == "openai" else "Gemini(외부)"


def is_external() -> bool:
    """회사 밖 서비스로 데이터가 나가는지 (화면 경고용). 사내 API 방식이면 False."""
    return provider() == "gemini"


def is_configured() -> bool:
    """AI를 쓸 수 있게 설정되어 있는지."""
    if provider() == "openai":
        return bool(os.getenv("LLM_BASE_URL", "").strip())
    return bool(os.getenv("LLM_API_KEY", "").strip())


def model_name() -> str:
    """사용할 모델 이름 (.env의 LLM_MODEL)."""
    m = os.getenv("LLM_MODEL", "").strip()
    return m or (DEFAULT_MODEL if provider() == "gemini" else "")


def fallback_model_name() -> str:
    """기본 모델이 혼잡할 때 쓸 예비 모델 (.env의 LLM_FALLBACK_MODEL, 쉼표로 여러 개, 비우면 예비 없음)."""
    default = DEFAULT_FALLBACK_MODEL if provider() == "gemini" else ""
    return os.getenv("LLM_FALLBACK_MODEL", default).strip()


def fallback_models() -> list[str]:
    return [m.strip() for m in fallback_model_name().split(",") if m.strip()]


def _no_quota(e: "_HTTPError") -> bool:
    """무료 한도를 다 썼거나(또는 한도 0) 쓸 수 없는 모델인지 — 기다려도 소용없으니 바로 다음 모델로."""
    msg = str(e.message).lower()
    return e.code == 429 and ("limit: 0" in msg or "check your plan" in msg or "billing" in msg
                              or "exceeded your current quota" in msg)


def last_model() -> str:
    """마지막으로 답한 모델 이름 (화면에 '어느 모델이 답했는지' 보여 줄 때)."""
    return _last_model


def model_status() -> list[dict]:
    """모델별 상태: 쉬는 중이면 남은 초."""
    now = time.time()
    names = [model_name()] + [m for m in fallback_models() if m != model_name()]
    return [{"model": m, "resting": max(0, round(_cooldown.get(m, 0) - now))} for m in names]


def _image_jpeg(path: str | Path) -> bytes:
    """스크린샷을 가로 IMAGE_MAX_WIDTH 이하 JPEG로 줄인다."""
    from PIL import Image
    with Image.open(path) as img:
        img = img.convert("RGB")
        if img.width > IMAGE_MAX_WIDTH:
            img = img.resize((IMAGE_MAX_WIDTH, round(img.height * IMAGE_MAX_WIDTH / img.width)))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
    return buf.getvalue()


ImageArg = str | Path | tuple[str, str | Path]   # 경로, 또는 (설명 문구, 경로)


# ------------------------------------------------------------------ Gemini

_client = None


def get_client():
    """Gemini 클라이언트를 한 번만 만들어 재사용한다."""
    global _client
    if _client is None:
        key = os.getenv("LLM_API_KEY", "").strip()
        if not key:
            raise LLMError(".env에 LLM_API_KEY가 없습니다. .env.example을 복사해 키를 넣으세요.")
        from google import genai
        from google.genai import types
        # 응답이 없을 때 끝없이 기다리지 않게 (밀리초). 넘으면 재시도
        timeout_ms = int(float(os.getenv("LLM_TIMEOUT", "120")) * 1000)
        _client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=timeout_ms))
    return _client


def _gemini_call(model: str, prompt: str, images: list[ImageArg], json_mode: bool, temperature: float,
                 timeout: float | None = None) -> str:
    from google.genai import errors, types
    contents: list = []
    for item in images:
        if isinstance(item, tuple):
            contents.append(item[0])
            item = item[1]
        contents.append(types.Part.from_bytes(data=_image_jpeg(item), mime_type="image/jpeg"))
    contents.append(prompt)
    config = types.GenerateContentConfig(temperature=temperature,
                                         response_mime_type="application/json" if json_mode else "text/plain",
                                         http_options=types.HttpOptions(timeout=int(timeout * 1000)) if timeout else None)
    try:
        resp = get_client().models.generate_content(model=model, contents=contents, config=config)
    except errors.APIError as e:
        raise _HTTPError(e.code, e.message) from e
    except Exception as e:   # 시간 초과·연결 끊김 → 일시 오류로 보고 재시도
        if "timeout" in type(e).__name__.lower() or "timed out" in str(e).lower() or "connect" in type(e).__name__.lower():
            raise _HTTPError(504, f"응답 시간 초과 ({type(e).__name__})") from e
        raise
    if not resp.text:
        raise LLMError("AI 응답이 비어 있습니다 (안전 필터 또는 모델 오류).")
    return resp.text


# ------------------------------------------------------------------ OpenAI 호환 (사내 API)

_json_format_ok = True   # 사내 서버가 response_format(JSON 모드)을 모르면 한 번 실패 후 끈다


def _openai_request(path: str, body: dict | None = None, timeout: float | None = None) -> dict:
    base = os.getenv("LLM_BASE_URL", "").strip().rstrip("/")
    if not base:
        raise LLMError(".env에 LLM_BASE_URL(사내 API 주소)이 없습니다. 예: LLM_BASE_URL=https://ai.사내도메인/v1")
    headers = {"Content-Type": "application/json"}
    key = os.getenv("LLM_API_KEY", "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8") if body is not None else None,
                                 headers=headers, method="POST" if body is not None else "GET")
    ctx = None if _flag("LLM_VERIFY_SSL", True) else ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout or float(os.getenv("LLM_TIMEOUT", "120")), context=ctx) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise _HTTPError(e.code, detail) from e
    except TimeoutError as e:
        raise _HTTPError(504, "응답 시간 초과") from e
    except urllib.error.URLError as e:
        if "timed out" in str(e.reason).lower():
            raise _HTTPError(504, "응답 시간 초과") from e
        raise LLMError(f"사내 AI 서버에 연결하지 못했습니다 ({base}): {e.reason}. 주소·사내망·프록시를 확인하세요.") from e


def _openai_call(model: str, prompt: str, images: list[ImageArg], json_mode: bool, temperature: float,
                 timeout: float | None = None) -> str:
    global _json_format_ok
    content: list[dict] = []
    for item in images:
        if isinstance(item, tuple):
            content.append({"type": "text", "text": item[0]})
            item = item[1]
        url = "data:image/jpeg;base64," + base64.b64encode(_image_jpeg(item)).decode()
        content.append({"type": "image_url", "image_url": {"url": url}})
    content.append({"type": "text", "text": prompt})
    body = {"messages": [{"role": "user", "content": content if images else prompt}], "temperature": temperature}
    if model:
        body["model"] = model
    if json_mode and _json_format_ok:
        body["response_format"] = {"type": "json_object"}
    try:
        data = _openai_request("/chat/completions", body, timeout)
    except _HTTPError as e:
        if e.code == 400 and "response_format" in body:   # JSON 모드를 모르는 서버 → 빼고 다시
            _json_format_ok = False
            body.pop("response_format")
            data = _openai_request("/chat/completions", body, timeout)
        else:
            raise
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"사내 AI 응답 형식을 알 수 없습니다: {str(data)[:300]}")
    if isinstance(text, list):   # 일부 서버는 조각 목록으로 돌려줌
        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
    if not text:
        raise LLMError("AI 응답이 비어 있습니다.")
    if json_mode:   # ```json … ``` 으로 감싸 오는 모델 대비
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
    return text


# ------------------------------------------------------------------ 공통

def _order() -> list[str]:
    """시도할 모델 순서: 설정 순서(좋은 모델 먼저)에서 쉬는 중인 모델은 뒤로 보낸다."""
    _load_cooldown()
    models = [model_name()]
    for fb in fallback_models():
        if fb not in models:
            models.append(fb)
    now = time.time()
    ready = [m for m in models if _cooldown.get(m, 0) <= now]
    resting = sorted((m for m in models if m not in ready), key=lambda m: _cooldown[m])
    return ready + resting


def generate(prompt: str, images: list[ImageArg] | None = None,
             json_mode: bool = False, temperature: float = 0.2, timeout: float | None = None) -> str:
    """프롬프트(+이미지)를 보내고 응답 텍스트를 돌려준다.

    - 좋은 모델부터 시도하고, 한도 초과·혼잡·시간 초과면 그 모델을 잠시 쉬게 하고 **바로** 다음 모델로 넘어간다.
      (같은 모델에 재시도하며 몇 분씩 기다리지 않는다. 쉬는 시간이 지나면 다시 좋은 모델부터 쓴다)
    - 남은 모델이 하나뿐일 때만 기다렸다 재시도한다.
    - timeout: 모델 한 번의 대기 시간(초). 화면에서 바로 쓰는 기능은 INTERACTIVE_TIMEOUT을 준다.
    images에 (설명, 경로)를 주면 이미지 바로 앞에 설명 문구를 붙여 보낸다.
    LLM_SEND_IMAGES=false면 이미지는 빼고 글만 보낸다 (이미지를 못 받는 사내 모델용).
    """
    global _last_model
    images = list(images or []) if _flag("LLM_SEND_IMAGES", True) else []
    call = _openai_call if provider() == "openai" else _gemini_call
    name = "사내 AI" if provider() == "openai" else "Gemini API"
    models = _order()
    last_err: _HTTPError | None = None

    for mi, model in enumerate(models):
        delay = 3.0 if timeout else 5.0
        retries = 1 if mi + 1 < len(models) else (2 if timeout else MAX_RETRIES)
        for attempt in range(1, retries + 1):
            try:
                text = call(model, prompt, images, json_mode, temperature, timeout)
                if _cooldown.pop(model, None):
                    _save_cooldown()
                if model != _last_model:
                    print(f"[llm] 응답 모델: {model}")
                _last_model = model
                return text
            except _HTTPError as e:
                last_err = e
                quota = _no_quota(e) or e.code in (403, 404)   # 쓸 수 없는 모델도 오래 쉬게
                if quota or e.code in RETRYABLE:
                    _cooldown[model] = time.time() + (COOLDOWN_QUOTA if quota else COOLDOWN_BUSY)
                    _save_cooldown()
                    why = "무료 한도 초과" if quota else ("응답 시간 초과" if e.code == 504 else f"혼잡·일시 오류 {e.code}")
                    if mi + 1 < len(models):
                        print(f"[llm] {model}: {why} → 잠시 쉬게 하고 {models[mi + 1]}로 넘어갑니다")
                        break
                    if not quota and attempt < retries:
                        print(f"[llm] {model}: {why}, {delay:.0f}초 후 재시도 ({attempt}/{retries - 1})")
                        time.sleep(delay)
                        delay *= 2
                        continue
                hint = {401: " (API 키를 확인하세요.)", 403: " (이 키로 쓸 수 없는 모델이거나 권한이 없습니다.)",
                        429: " (모든 모델의 무료 사용 한도를 넘었습니다. 잠시 뒤 또는 내일 다시 시도하세요.)",
                        503: " (모든 모델이 혼잡합니다. 잠시 후 다시 시도하세요.)",
                        504: " (모든 모델의 응답이 늦습니다. 잠시 후 다시 시도하세요.)",
                        404: " (python -m analyzer.llm_client --list 로 쓸 수 있는 모델을 확인해 .env의 LLM_MODEL을 바꾸세요.)",
                        }.get(e.code, "")
                raise LLMError(f"{name} 오류 {e.code}: {e.message}{hint}") from e
    raise LLMError(f"{name}: 쓸 수 있는 모델이 없습니다 ({last_err.message if last_err else ''})")


def generate_json(prompt: str, images: list[ImageArg] | None = None, timeout: float | None = None):
    """JSON 응답을 받아 파싱한다. 형식이 틀리면 한 번 재요청하고, 그래도 틀리면 LLMError."""
    text = generate(prompt, images, json_mode=True, timeout=timeout)
    try:
        return json.loads(text)
    except json.JSONDecodeError as first:
        retry_prompt = (prompt + "\n\n[주의] 이전 응답이 올바른 JSON이 아니었습니다. "
                        "설명 없이 JSON만 출력하세요.")
        text = generate(retry_prompt, images, json_mode=True, timeout=timeout)
        try:
            return json.loads(text)
        except json.JSONDecodeError as second:
            raise LLMError(f"AI 응답이 JSON 형식이 아닙니다 (2회 시도). 첫 오류: {first}, "
                           f"두 번째 오류: {second}\n응답 앞부분: {text[:300]}") from second


def list_models() -> list[str]:
    """이 설정으로 쓸 수 있는 모델 이름 목록."""
    if provider() == "openai":
        try:
            data = _openai_request("/models")
        except _HTTPError as e:
            raise LLMError(f"모델 목록을 받지 못했습니다 ({e.code}): {e.message}") from e
        return sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))
    names = []
    for m in get_client().models.list():
        actions = getattr(m, "supported_actions", None) or []
        if not actions or "generateContent" in actions:
            names.append(m.name.removeprefix("models/"))
    return sorted(names)


def main() -> None:
    """연결 확인용 명령."""
    parser = argparse.ArgumentParser(description="AI(LLM) 연결 확인")
    parser.add_argument("--list", action="store_true", help="사용 가능한 모델 목록 출력")
    args = parser.parse_args()
    try:
        print(f"연결 방식: {provider_label()}")
        if args.list:
            print("\n".join(list_models()))
            return
        print(f"모델: {model_name() or '(서버 기본 모델)'}")
        print("응답:", generate("한 문장으로 인사해줘.").strip())
        print("연결 성공")
    except LLMError as e:
        print(f"실패: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
