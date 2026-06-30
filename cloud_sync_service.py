import base64
import json
import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Callable

from config import update_game_fields
from constants import DEFAULT_REMOTE_ZIP_PATH, PENDING_BACKUP_DIR_NAME
from providers import create_provider, normalized_repo_name, provider_display_name, provider_type_from_config
from providers.base import metadata_path_for_zip
from save_manager import (
    collect_files,
    copy_directory_snapshot,
    extract_zip_with_timestamps,
    read_and_encode_file,
    replace_directory_contents,
    sha256_of_file,
    validate_zip_members,
    zip_save_directory,
)
from utils import default_device_name, format_size, now_text, remote_zip_path_from_input, transfer_status


ProgressCallback = Callable[[float, str], None]
LogCallback = Callable[[str], None]


def _ignore_log(_message: str) -> None:
    return


def get_game_by_id(config_data: dict, game_id: str) -> dict:
    for game in config_data.get("games", []):
        if str(game.get("id", "")) == game_id:
            return game
    raise RuntimeError(f"找不到游戏配置：{game_id}")


def validate_sync_inputs(config_data: dict, game_id: str) -> tuple[dict, str, str, str, Path, str]:
    game = get_game_by_id(config_data, game_id)
    token = str(config_data.get("token", "")).strip()
    repo = normalized_repo_name(config_data).strip()
    branch = str(config_data.get("branch", "main") or "main").strip() or "main"
    save_path = str(game.get("save_path", "")).strip()
    remote_zip_path = remote_zip_path_from_input(str(game.get("remote_zip_path", DEFAULT_REMOTE_ZIP_PATH)))
    provider_name = provider_display_name(provider_type_from_config(config_data))

    if not token:
        raise RuntimeError(f"缺少 {provider_name} Token。请先在 GameCloudSave 中保存配置。")
    if not repo:
        raise RuntimeError(f"缺少 {provider_name} 仓库名。请先在 GameCloudSave 中保存配置。")
    if not save_path:
        raise RuntimeError("缺少本地存档目录。请先在 GameCloudSave 中保存配置。")
    if not remote_zip_path:
        raise RuntimeError("云端存档路径生成失败。请先在 GameCloudSave 中保存配置。")

    save_dir = Path(save_path)
    if not save_dir.exists() or not save_dir.is_dir():
        raise RuntimeError(f"本地存档目录不存在：\n{save_path}")
    return game, token, repo, branch, save_dir, remote_zip_path


def get_remote_info(config_data: dict, game_id: str) -> dict:
    game = get_game_by_id(config_data, game_id)
    provider = create_provider(config_data)
    token = str(config_data.get("token", "")).strip()
    repo = normalized_repo_name(config_data).strip()
    branch = str(config_data.get("branch", "main") or "main").strip() or "main"
    zip_path = remote_zip_path_from_input(str(game.get("remote_zip_path", DEFAULT_REMOTE_ZIP_PATH)))
    if not zip_path:
        raise RuntimeError("云端存档路径生成失败。请先在 GameCloudSave 中保存配置。")
    try:
        return provider.get_remote_metadata(token, repo, metadata_path_for_zip(zip_path), branch)
    except RuntimeError as exc:
        if "404" in str(exc) or "找不到文件" in str(exc):
            return {"uploaded_at": "", "not_uploaded": True}
        raise


def build_upload_metadata(device_name: str, zip_sha256: str, zip_size: int) -> dict:
    return {
        "uploaded_at": now_text(),
        "device_name": device_name,
        "zip_sha256": zip_sha256,
        "zip_size": zip_size,
    }


def upload_game_archive(
    config_data: dict,
    game_id: str,
    emit_progress: ProgressCallback,
    emit_log: LogCallback = _ignore_log,
) -> dict:
    temp_zip: str | None = None
    try:
        provider = create_provider(config_data)
        game, token, repo, branch, save_dir, zip_path = validate_sync_inputs(config_data, game_id)

        emit_progress(5, "正在扫描本地存档文件...")
        files, total_bytes = collect_files(save_dir)
        if not files:
            raise RuntimeError("本地存档目录里没有可上传的文件。")
        emit_log(f"准备打包 {len(files)} 个文件，总大小约 {format_size(total_bytes)}。")

        emit_progress(10, "正在打包存档...")
        temp_zip = zip_save_directory(save_dir, files, total_bytes, emit_progress)
        zip_sha256 = sha256_of_file(temp_zip)
        zip_size = os.path.getsize(temp_zip)
        emit_log(f"压缩包已生成：{format_size(zip_size)}，SHA256={zip_sha256[:16]}...")

        emit_progress(52, transfer_status("正在读取压缩包内容...", file_size=format_size(zip_size)))
        zip_encoded = read_and_encode_file(
            temp_zip,
            start_percent=52,
            end_percent=68,
            status="正在编码 zip 压缩包...",
            emit_progress=emit_progress,
        )

        emit_progress(74, f"正在检查 {provider.display_name} 上是否已有旧备份...")
        existing_zip_sha = provider.get_existing_file_sha(token, repo, zip_path, branch)

        emit_progress(82, transfer_status("正在上传 zip 存档包...", file_size=format_size(zip_size)))
        upload_speed = provider.upload_file(
            token,
            repo,
            zip_path,
            branch,
            zip_encoded,
            f"Update Games cloud save zip {time.strftime('%Y-%m-%d %H:%M:%S')}",
            existing_zip_sha,
        )
        emit_log(f"上传完成：{repo}/{zip_path}")
        return {
            "repo": repo,
            "branch": branch,
            "zip_path": zip_path,
            "upload_speed": upload_speed,
            "zip_size": zip_size,
            "metadata": build_upload_metadata(default_device_name(), zip_sha256, zip_size),
        }
    finally:
        if temp_zip and os.path.exists(temp_zip):
            try:
                os.remove(temp_zip)
            except OSError:
                pass


def update_game_metadata(
    config_data: dict,
    game_id: str,
    config_path: Path,
    metadata: dict,
    emit_progress: ProgressCallback,
    emit_log: LogCallback = _ignore_log,
    upload_speed: float | None = None,
) -> dict:
    provider = create_provider(config_data)
    game, token, repo, branch, _save_dir, zip_path = validate_sync_inputs(config_data, game_id)
    json_path = metadata_path_for_zip(zip_path)
    emit_progress(
        92,
        transfer_status(
            "正在上传元数据 json...",
            speed=f"{format_size(int(upload_speed or 0))}/s" if upload_speed else None,
            file_size=format_size(int(metadata.get("zip_size", 0))) if metadata.get("zip_size") else None,
        ),
    )
    json_encoded = base64.b64encode(
        json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
    ).decode("ascii")
    existing_json_sha = provider.get_existing_file_sha(token, repo, json_path, branch)
    provider.upload_file(
        token,
        repo,
        json_path,
        branch,
        json_encoded,
        f"Update Games cloud save metadata {time.strftime('%Y-%m-%d %H:%M:%S')}",
        existing_json_sha,
    )

    game["last_uploaded_at"] = str(metadata["uploaded_at"])
    update_game_fields(
        config_path,
        game_id,
        {
            "last_uploaded_at": str(metadata["uploaded_at"]),
            "last_downloaded_zip_sha256": str(metadata.get("zip_sha256", "")).strip(),
        },
    )
    emit_log(f"元数据已更新：{repo}/{json_path}")
    emit_progress(100, "上传完成")
    return {
        "message": "上传完成，云端存档和元数据都已更新。",
        "metadata": metadata,
    }


def upload_game(
    config_data: dict,
    game_id: str,
    config_path: Path,
    emit_progress: ProgressCallback,
    emit_log: LogCallback = _ignore_log,
) -> dict:
    archive_result = upload_game_archive(config_data, game_id, emit_progress, emit_log)
    return update_game_metadata(
        config_data,
        game_id,
        config_path,
        archive_result["metadata"],
        emit_progress,
        emit_log,
        archive_result["upload_speed"],
    )


def download_game(
    config_data: dict,
    game_id: str,
    config_path: Path,
    data_dir: Path,
    emit_progress: ProgressCallback,
    emit_log: LogCallback = _ignore_log,
) -> dict:
    temp_zip: str | None = None
    temp_extract_dir: str | None = None
    pending_state: dict | None = None
    try:
        provider = create_provider(config_data)
        game, token, repo, branch, save_dir, zip_path = validate_sync_inputs(config_data, game_id)
        emit_progress(8, "正在读取云端下载信息...")
        emit_progress(18, "正在下载云端 zip 压缩包...")
        temp_zip = provider.download_file(token, repo, zip_path, branch, emit_progress)
        zip_size = os.path.getsize(temp_zip)
        zip_sha256 = sha256_of_file(temp_zip)
        emit_log(f"云端压缩包下载完成：{format_size(zip_size)}，SHA256={zip_sha256[:16]}...")

        emit_progress(60, "正在校验压缩包结构...")
        validate_zip_members(temp_zip)
        emit_progress(72, "正在解压云端存档...")
        temp_extract_dir = tempfile.mkdtemp(prefix="games_save_extract_")
        with zipfile.ZipFile(temp_zip, "r") as archive:
            extract_zip_with_timestamps(archive, Path(temp_extract_dir))

        extracted_root = Path(temp_extract_dir) / save_dir.name
        if not extracted_root.exists():
            extracted_root = Path(temp_extract_dir)

        destination_dir = save_dir
        emit_progress(78, "正在备份当前本地存档...")
        pending_root = data_dir / PENDING_BACKUP_DIR_NAME / game_id
        snapshot_dir = pending_root / "save_data"
        if pending_root.exists():
            shutil.rmtree(pending_root, ignore_errors=True)
        pending_root.mkdir(parents=True, exist_ok=True)
        copy_directory_snapshot(save_dir, snapshot_dir)
        pending_state = {
            "backup_dir": str(snapshot_dir),
            "source_save_dir": str(save_dir),
            "created_at": now_text(),
        }
        game["pending_restore"] = pending_state
        update_game_fields(
            config_path,
            game_id,
            {
                "pending_restore": pending_state,
                "last_downloaded_zip_sha256": zip_sha256,
            },
        )
        emit_log(f"已生成最近一次下载前备份：{snapshot_dir}")
        emit_log(f"下载模式：备份后覆盖本地存档 -> {destination_dir}")

        emit_progress(82, "正在用云端存档覆盖本地存档...")
        replace_directory_contents(extracted_root, destination_dir)
        emit_progress(98, "本地存档覆盖完成")
        emit_log(f"已用云端存档完整覆盖本地目录：{destination_dir}")

        game["pending_restore"] = pending_state
        update_game_fields(
            config_path,
            game_id,
            {
                "pending_restore": pending_state,
                "last_downloaded_zip_sha256": zip_sha256,
            },
        )
        message = (
            f"下载完成，已备份原本地存档并用云端存档覆盖：\n{destination_dir}"
            "\n\n最近一次下载前备份可随时在主程序中回退。"
        )
        emit_progress(100, "下载完成")
        return {
            "message": message,
            "pending_restore": pending_state,
            "zip_sha256": zip_sha256,
        }
    finally:
        if temp_zip and os.path.exists(temp_zip):
            try:
                os.remove(temp_zip)
            except OSError:
                pass
        if temp_extract_dir and os.path.exists(temp_extract_dir):
            shutil.rmtree(temp_extract_dir, ignore_errors=True)


def rollback_game(
    config_data: dict,
    game_id: str,
    config_path: Path,
    emit_progress: ProgressCallback,
) -> dict:
    game = get_game_by_id(config_data, game_id)
    pending_state = game.get("pending_restore")
    if not isinstance(pending_state, dict):
        raise RuntimeError("当前没有可回退的本地副本。")
    backup_dir = Path(str(pending_state.get("backup_dir", "")))
    save_dir = Path(str(pending_state.get("source_save_dir", "")))
    if not backup_dir.exists():
        raise RuntimeError("本地副本已不存在，无法回退。")

    emit_progress(10, "正在回退到下载前的本地副本...")
    replace_directory_contents(backup_dir, save_dir)
    emit_progress(90, "正在清理本地副本...")
    pending_root = backup_dir.parent
    if pending_root.exists():
        shutil.rmtree(pending_root, ignore_errors=True)
    game["pending_restore"] = None
    update_game_fields(config_path, game_id, {"pending_restore": None, "last_downloaded_zip_sha256": ""})
    emit_progress(100, "回退完成")
    return {"message": "已回退到最近一次下载前的本地备份，该备份已删除。"}
