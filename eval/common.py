"""평가 공통: 시나리오·인덱스 파일 읽기/쓰기."""
import json
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parent
SCENARIOS = EVAL_DIR / "scenarios.json"
INDEX = EVAL_DIR / "recordings_index.json"   # 시나리오 id → 녹화 폴더 / 매크로 파일
RESULTS_DIR = ROOT / "docs" / "results"


def load_scenarios(only: list[str] | None = None) -> dict:
    """시나리오 파일을 읽는다. only를 주면 그 id만 남긴다."""
    data = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    if only:
        data["scenarios"] = [s for s in data["scenarios"] if s["id"] in only]
    return data


def load_index() -> dict:
    """녹화 인덱스 읽기 (없으면 빈 dict)."""
    return json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {}


def save_index(index: dict) -> None:
    """녹화 인덱스 저장."""
    INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
