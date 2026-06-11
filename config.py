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
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def update_current_game_id(config_path: Path, game_id: str) -> dict:
    latest = load_config(config_path)
    latest.pop("emulator_path", None)
    games = latest.get("games", [])
    if not any(str(game.get("id", "")) == game_id for game in games if isinstance(game, dict)):
        raise RuntimeError(f"找不到游戏配置：{game_id}")
    latest["current_game_id"] = game_id
    save_config(config_path, latest)
    return latest


def update_game_fields(config_path: Path, game_id: str, fields: dict) -> dict:
    latest = load_config(config_path)
    latest.pop("emulator_path", None)
    for game in latest.get("games", []):
        if isinstance(game, dict) and str(game.get("id", "")) == game_id:
            game.update(fields)
            save_config(config_path, latest)
            return latest
    raise RuntimeError(f"找不到游戏配置：{game_id}")
