import json
from pathlib import Path


def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(config_path: Path, data: dict) -> None:
    config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
