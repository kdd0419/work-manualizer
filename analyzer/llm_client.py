"""LLM 호출은 이 파일 한 곳에서만 한다. (Google Gemini API, google-genai SDK)

설정(.env):
  LLM_API_KEY  Google AI Studio에서 받은 키
  LLM_MODEL           모델 이름 (기본: gemini-3.5-flash. 404 '모델 없음' 오류가 나면 --list로 확인해 바꾼다)
  LLM_FALLBACK_MODEL  기본 모델이 혼잡(503)할 때 쓸 예비 모델 (기본: gemini-3.8-flash)

연결 확인:
  python -m analyzer.llm_client          # 짧은 질문으로 응답 확인
  python -m analyzer.llm_client --list   # 이 키로 쓸 수 있는 모델 목록
"""
import argparse
import io
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# 2026-11 기준 새 키에서 쓸 수 있는 무료 모델 (gemini-2.5-flash는 새 사용자 불가, 3.8-flash는 혼잡(503)이 잦음)
DEFAULT_MODEL = "gemini-3.5-flash"
DEFAULT_FALLBACK_MODEL = "gemini-3.8-flash"
MAX_RETRIES = 4          # 429(한도 초과)·일시 오류 재시도 횟수
IMAGE_MAX_WIDTH = 1024   # 비용·한도 절약을 위해 스크린샷을 이 폭으로 줄여 보낸다


class LLMError(RuntimeError):
    """LLM 호출 실패 (키 없음, 한도 초과 지속, JSON 형식 위반 등)."""


_client = None


def get_client():
    """Gemini 클라이언트를 한 번만 만들어 재사용한다."""
    global _client
    if _client is None:
        key = os.getenv("LLM_API_KEY", "").strip()
        if not key:
            raise LLMError(".env에 LLM_API_KEY가 없습니다. .env.example을 복사해 키를 넣으세요.")
        from google import genai
        _client = genai.Client(api_key=key)
    return _client


def model_name() -> str:
    """사용할 모델 이름 (.env의 LLM_MODEL)."""
    return os.getenv("LLM_MODEL", "").strip() or DEFAULT_MODEL


def fallback_model_name() -> str:
    """기본 모델이 혼잡할 때 쓸 예비 모델 (.env의 LLM_FALLBACK_MODEL, 비우면 예비 없음)."""
    return os.getenv("LLM_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL).strip()


def _image_part(path: str | Path):
    """스크린샷을 가로 IMAGE_MAX_WIDTH 이하 JPEG로 줄여 요청용 Part로 만든다."""
    from google.genai import types
    from PIL import Image

    with Image.open(path) as img:
        img = img.convert("RGB")
        if img.width > IMAGE_MAX_WIDTH:
            h = round(img.height * IMAGE_MAX_WIDTH / img.width)
            img = img.resize((IMAGE_MAX_WIDTH, h))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
    return types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg")


ImageArg = str | Path | tuple[str, str | Path]   # 경로, 또는 (설명 문구, 경로)


def generate(prompt: str, images: list[ImageArg] | None = None,
             json_mode: bool = False, temperature: float = 0.2) -> str:
    """프롬프트(+이미지)를 보내고 응답 텍스트를 돌려준다. 한도 초과 시 기다렸다 재시도.

    images에 (설명, 경로)를 주면 이미지 바로 앞에 설명 문구를 붙여 보낸다.
    """
    from google.genai import errors, types

    contents: list = []
    for item in images or []:
        if isinstance(item, tuple):
            contents.append(item[0])
            item = item[1]
        contents.append(_image_part(item))
    contents.append(prompt)
    config = types.GenerateContentConfig(
        temperature=temperature,
        response_mime_type="application/json" if json_mode else "text/plain",
    )

    # 기본 모델이 혼잡(503)·일시 오류(500)로 계속 실패하면 예비 모델로 한 번 더 시도한다
    models = [model_name()]
    fallback = fallback_model_name()
    if fallback and fallback not in models:
        models.append(fallback)

    for mi, model in enumerate(models):
        delay = 5.0
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = get_client().models.generate_content(model=model, contents=contents, config=config)
                if not resp.text:
                    raise LLMError("AI 응답이 비어 있습니다 (안전 필터 또는 모델 오류).")
                return resp.text
            except errors.APIError as e:
                retryable = e.code in (429, 500, 503)
                if retryable and attempt < MAX_RETRIES:
                    print(f"[llm] {e.code} 오류, {delay:.0f}초 후 재시도 ({attempt}/{MAX_RETRIES - 1})")
                    time.sleep(delay)
                    delay *= 2
                    continue
                if e.code in (500, 503) and mi + 1 < len(models):
                    print(f"[llm] {model} 모델이 혼잡해 예비 모델 {models[mi + 1]}(으)로 다시 시도합니다")
                    break
                hint = {429: " (무료 등급 한도 초과일 수 있습니다. 잠시 후 다시 시도하세요.)",
                        503: " (모델이 혼잡합니다. 잠시 후 다시 시도하거나 .env의 LLM_MODEL을 바꾸세요.)",
                        404: " (python -m analyzer.llm_client --list 로 쓸 수 있는 모델을 확인해 .env의 LLM_MODEL을 바꾸세요.)",
                        }.get(e.code, "")
                raise LLMError(f"Gemini API 오류 {e.code}: {e.message}{hint}") from e
    raise LLMError("재시도 횟수 초과")  # 도달하지 않음


def generate_json(prompt: str, images: list[ImageArg] | None = None):
    """JSON 응답을 받아 파싱한다. 형식이 틀리면 한 번 재요청하고, 그래도 틀리면 LLMError."""
    text = generate(prompt, images, json_mode=True)
    try:
        return json.loads(text)
    except json.JSONDecodeError as first:
        retry_prompt = (prompt + "\n\n[주의] 이전 응답이 올바른 JSON이 아니었습니다. "
                        "설명 없이 JSON만 출력하세요.")
        text = generate(retry_prompt, images, json_mode=True)
        try:
            return json.loads(text)
        except json.JSONDecodeError as second:
            raise LLMError(f"AI 응답이 JSON 형식이 아닙니다 (2회 시도). 첫 오류: {first}, "
                           f"두 번째 오류: {second}\n응답 앞부분: {text[:300]}") from second


def list_models() -> list[str]:
    """이 키로 텍스트 생성에 쓸 수 있는 모델 이름 목록."""
    names = []
    for m in get_client().models.list():
        actions = getattr(m, "supported_actions", None) or []
        if not actions or "generateContent" in actions:
            names.append(m.name.removeprefix("models/"))
    return sorted(names)


def main() -> None:
    """연결 확인용 명령."""
    parser = argparse.ArgumentParser(description="Gemini API 연결 확인")
    parser.add_argument("--list", action="store_true", help="사용 가능한 모델 목록 출력")
    args = parser.parse_args()
    try:
        if args.list:
            print("\n".join(list_models()))
            return
        print(f"모델: {model_name()}")
        print("응답:", generate("한 문장으로 인사해줘.").strip())
        print("연결 성공")
    except LLMError as e:
        print(f"실패: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
