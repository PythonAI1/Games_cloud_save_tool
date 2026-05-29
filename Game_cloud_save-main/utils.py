import platform
import re
import time


def format_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"


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
