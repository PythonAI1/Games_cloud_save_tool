import base64
import hashlib
import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

from utils import format_timestamp


def snapshot_save_directory(save_dir: Path) -> str:
    if not save_dir.exists() or not save_dir.is_dir():
        raise RuntimeError(f"本地存档目录不存在：\n{save_dir}")

    digest = hashlib.sha256()
    files = sorted((path for path in save_dir.rglob("*") if path.is_file()), key=lambda path: path.as_posix())
    for file_path in files:
        relative = file_path.relative_to(save_dir).as_posix().encode("utf-8")
        size = file_path.stat().st_size
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(size.to_bytes(8, "big"))
        with file_path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 256)
                if not chunk:
                    break
                digest.update(chunk)
    return digest.hexdigest()


def collect_files(save_dir: Path) -> tuple[list[Path], int]:
    files: list[Path] = []
    total = 0
    for root, _, names in os.walk(save_dir):
        for name in names:
            path = Path(root) / name
            files.append(path)
            total += path.stat().st_size
    return files, total


def zip_save_directory(save_dir: Path, files: list[Path], total_bytes: int, emit_progress) -> str:
    fd, temp_zip = tempfile.mkstemp(prefix="games_save_", suffix=".zip")
    os.close(fd)

    processed = 0
    with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, file_path in enumerate(files, start=1):
            relative = file_path.relative_to(save_dir.parent)
            archive.write(file_path, arcname=str(relative))
            processed += file_path.stat().st_size
            percent = 10 + (processed / max(total_bytes, 1)) * 40
            emit_progress(percent, f"正在打包存档... {index}/{len(files)}")
    return temp_zip


def read_and_encode_file(file_path: str, start_percent: float, end_percent: float, status: str, emit_progress) -> str:
    size = os.path.getsize(file_path)
    processed = 0
    buffer = bytearray()
    with open(file_path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 256)
            if not chunk:
                break
            buffer.extend(chunk)
            processed += len(chunk)
            progress = start_percent + (processed / max(size, 1)) * (end_percent - start_percent)
            emit_progress(progress, status)
    return base64.b64encode(buffer).decode("ascii")


def copy_tree_with_progress(source: Path, destination: Path, emit_progress) -> int:
    files: list[Path] = []
    for root, _, names in os.walk(source):
        for name in names:
            files.append(Path(root) / name)

    if not files:
        raise RuntimeError("解压后的备份里没有文件，无法恢复。")

    total = len(files)
    for index, src_file in enumerate(files, start=1):
        relative = src_file.relative_to(source)
        dst_file = destination / relative
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)
        percent = 82 + (index / total) * 16
        emit_progress(percent, f"正在写入本地存档文件... {index}/{total}")
    return total


def copy_directory_snapshot(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination, ignore_errors=True)
    shutil.copytree(source, destination, copy_function=shutil.copy2)


def replace_directory_contents(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for child in destination.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target, copy_function=shutil.copy2)
        else:
            shutil.copy2(child, target)


def extract_zip_with_timestamps(archive: zipfile.ZipFile, destination: Path) -> None:
    for member in archive.infolist():
        extracted_path = Path(archive.extract(member, path=destination))
        if member.is_dir():
            continue
        try:
            timestamp = time.mktime(member.date_time + (0, 0, -1))
            os.utime(extracted_path, (timestamp, timestamp))
        except (OverflowError, OSError, ValueError):
            pass


def build_new_save_folder(current_save_dir: Path) -> Path:
    parent = current_save_dir.parent
    base_name = f"{current_save_dir.name}_github_{time.strftime('%Y%m%d_%H%M%S')}"
    candidate = parent / base_name
    serial = 1
    while candidate.exists():
        candidate = parent / f"{base_name}_{serial}"
        serial += 1
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


def scan_slot_times(save_dir: Path) -> dict[str, str]:
    slots: dict[str, str] = {}
    for child in sorted(save_dir.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or not child.name.isdigit():
            continue
        latest = 0.0
        for root, _, names in os.walk(child):
            for name in names:
                file_path = Path(root) / name
                try:
                    latest = max(latest, file_path.stat().st_mtime)
                except OSError:
                    pass
        if latest > 0:
            slots[child.name] = format_timestamp(latest)
    return slots


def latest_slot_from_slots(slots: dict[str, str]) -> tuple[str, str]:
    if not slots:
        return "无", "无"
    latest_slot = max(slots.items(), key=lambda item: item[1])[0]
    return latest_slot, slots[latest_slot]


def path_mtime_text(path: Path) -> str:
    if not path.exists():
        return "未找到"
    try:
        return format_timestamp(path.stat().st_mtime)
    except OSError:
        return "读取失败"


def validate_zip_members(zip_path: str) -> None:
    with zipfile.ZipFile(zip_path, "r") as archive:
        for member in archive.infolist():
            member_path = Path(member.filename)
            if member_path.is_absolute():
                raise RuntimeError("压缩包里包含非法绝对路径，已拒绝恢复。")
            if ".." in member_path.parts:
                raise RuntimeError("压缩包里包含非法父目录跳转，已拒绝恢复。")


def sha256_of_file(file_path: str) -> str:
    digest = hashlib.sha256()
    with open(file_path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 256)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def find_best_save_path_in_root(root: Path, save_candidate_score) -> str:
    if not root.exists():
        return ""
    candidates: list[Path] = []
    try:
        for path in root.rglob("game_data.sav"):
            parent = path.parent
            if not parent.name.isdigit():
                continue
            save_root = parent.parent
            if (save_root / "option.sav").exists():
                candidates.append(save_root)
    except (OSError, RuntimeError):
        return ""

    if not candidates:
        return ""

    unique_candidates = []
    seen = set()
    for path in candidates:
        text = str(path.resolve())
        if text not in seen:
            seen.add(text)
            unique_candidates.append(path)

    unique_candidates.sort(key=save_candidate_score, reverse=True)
    return str(unique_candidates[0])


def scan_root_directory_for_save(root: Path, emit_progress, save_candidate_score) -> str:
    all_dirs: list[Path] = []
    for current_root, dirs, _ in os.walk(root):
        all_dirs.append(Path(current_root))
        for dir_name in dirs:
            _ = dir_name

    total = max(len(all_dirs), 1)
    candidates: list[Path] = []

    for index, current_dir in enumerate(all_dirs, start=1):
        percent = 5 + (index / total) * 85
        emit_progress(percent, f"正在扫描目录... {index}/{total}")
        try:
            if (current_dir / "option.sav").exists():
                slot_dirs = [child for child in current_dir.iterdir() if child.is_dir() and child.name.isdigit()]
                if slot_dirs:
                    for slot_dir in slot_dirs:
                        if (slot_dir / "game_data.sav").exists():
                            candidates.append(current_dir)
                            break
        except OSError:
            pass

    if not candidates:
        return ""

    unique_candidates = []
    seen = set()
    for path in candidates:
        text = str(path.resolve())
        if text not in seen:
            seen.add(text)
            unique_candidates.append(path)

    unique_candidates.sort(key=save_candidate_score, reverse=True)
    return str(unique_candidates[0])
