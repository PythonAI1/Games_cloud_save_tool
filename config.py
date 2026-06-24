import json
import base64
import binascii
import ctypes
import copy
from pathlib import Path


CRYPTPROTECT_UI_FORBIDDEN = 0x01


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_ulong),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _blob_from_bytes(value: bytes) -> DATA_BLOB:
    buffer = ctypes.create_string_buffer(value)
    blob = DATA_BLOB(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    blob._buffer = buffer
    return blob


def _bytes_from_blob(blob: DATA_BLOB) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def _protect_text(value: str) -> str:
    raw = value.encode("utf-8")
    input_blob = _blob_from_bytes(raw)
    output_blob = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    ):
        raise OSError("Failed to protect token")
    return base64.b64encode(_bytes_from_blob(output_blob)).decode("ascii")


def _unprotect_text(value: str) -> str:
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError):
        return ""
    input_blob = _blob_from_bytes(raw)
    output_blob = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    ):
        return ""
    try:
        return _bytes_from_blob(output_blob).decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _with_unprotected_token(data: dict) -> dict:
    token = str(data.get("token", "")).strip()
    protected = str(data.get("token_protected", "")).strip()
    if not token and protected:
        data["token"] = _unprotect_text(protected)
    return data


def _with_protected_token(data: dict) -> dict:
    stored = copy.deepcopy(data)
    token = str(stored.pop("token", "")).strip()
    stored.pop("token_protected", None)
    if token:
        stored["token_protected"] = _protect_text(token)
    return stored


def load_config(config_path: Path) -> dict:
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return _with_unprotected_token(data)
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(config_path: Path, data: dict) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    stored = _with_protected_token(data)
    config_path.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")


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
