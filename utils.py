import platform
import posixpath
import re
import time

from constants import REMOTE_ZIP_FILENAME


def format_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"


def format_speed(bytes_per_second: float) -> str:
    return f"{format_size(max(0, int(bytes_per_second)))}/s"


def transfer_status(status: str, speed: str | None = None, file_size: str | None = None) -> str:
    parts = [status]
    if speed:
        parts.append(f"网络速度：{speed}")
    if file_size:
        parts.append(f"ZIP 文件大小：{file_size}")
    return " | ".join(parts)


def parse_transfer_status(status: str) -> tuple[str, str | None, str | None]:
    parts = status.split(" | ")
    message = parts[0]
    speed = None
    file_size = None
    for part in parts[1:]:
        if part.startswith("网络速度："):
            speed = part.removeprefix("网络速度：")
        elif part.startswith("ZIP 文件大小："):
            file_size = part.removeprefix("ZIP 文件大小：")
    return message, speed, file_size


def remote_zip_path_from_input(value: str) -> str:
    value = value.strip().replace("\\", "/").strip("/")
    if not value:
        return ""
    if value.lower().endswith(".zip"):
        return value
    return f"{value}/{REMOTE_ZIP_FILENAME}"


def remote_zip_input_from_path(value: str) -> str:
    value = value.strip().replace("\\", "/").strip("/")
    if not value:
        return ""
    if value.lower().endswith(".zip"):
        parent = posixpath.dirname(value)
        return parent or value
    return value


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def format_timestamp(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


def parse_time_text(text: str) -> float | None:
    if not text or text in {"无", "未找到", "读取失败", "未知"}:
        return None
    try:
        return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None


def sanitize_device_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z一-鿿]", "", value or "")


def default_device_name() -> str:
    device_name = sanitize_device_name(platform.node())
    return device_name or "GamesDevice"
