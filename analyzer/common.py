"""analyzer 공통: 경로, 파일 읽기/쓰기, 프롬프트 불러오기."""
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECORDINGS_DIR = ROOT / "recordings"
MACROS_DIR = ROOT / "macros"
MANUALS_DIR = ROOT / "manuals"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def resolve_recording(rec: str | Path) -> Path:
    """'recordings/<id>' 경로 또는 id만 받아 녹화 폴더 경로로 바꾼다."""
    p = Path(rec)
    if not p.is_absolute() and not p.exists():
        p = RECORDINGS_DIR / rec
    if not (p / "events.json").exists():
        raise FileNotFoundError(f"녹화 폴더가 아니거나 events.json이 없습니다: {p}")
    return p.resolve()


def read_json(path: Path):
    """JSON 파일 읽기."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, data) -> Path:
    """JSON 파일 쓰기 (한글 그대로, 들여쓰기)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_prompt(name: str, version: str = "v1") -> str:
    """analyzer/prompts/<name>_<version>.txt를 읽는다."""
    path = PROMPTS_DIR / f"{name}_{version}.txt"
    if not path.exists():
        raise FileNotFoundError(f"프롬프트 파일이 없습니다: {path}")
    return path.read_text(encoding="utf-8")


def fill_prompt(template: str, **values) -> str:
    """프롬프트의 {{이름}} 자리를 값으로 채운다 (dict/list는 JSON으로)."""
    for key, val in values.items():
        if not isinstance(val, str):
            val = json.dumps(val, ensure_ascii=False, indent=1)
        template = template.replace("{{" + key + "}}", val)
    return template


def now_iso() -> str:
    """현재 시각 문자열."""
    return datetime.now().isoformat(timespec="seconds")
