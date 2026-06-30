import base64
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from utils import format_size, format_speed, transfer_status


def _repo_parts(repo: str) -> tuple[str, str]:
    owner, name = repo.strip().split("/", 1)
    return owner.strip(), name.strip()


def _contents_api_url(repo: str, remote_path: str, branch: str, token: str) -> str:
    owner, name = _repo_parts(repo)
    quoted_path = urllib.parse.quote(remote_path.strip("/"), safe="/")
    query = urllib.parse.urlencode({"access_token": token, "ref": branch})
    return f"https://gitee.com/api/v5/repos/{owner}/{name}/contents/{quoted_path}?{query}"


def _blob_api_url(repo: str, sha: str, token: str) -> str:
    owner, name = _repo_parts(repo)
    query = urllib.parse.urlencode({"access_token": token})
    return f"https://gitee.com/api/v5/repos/{owner}/{name}/git/blobs/{sha}?{query}"


def _extract_gitee_error(body: str) -> str:
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            message = data.get("message") or data.get("error_description") or data.get("error")
            if message:
                return str(message)
    except json.JSONDecodeError:
        pass
    return body.strip() or "未知错误"


def _unexpected_response_error(response: Any, remote_path: str) -> RuntimeError:
    if isinstance(response, list):
        if not response:
            return RuntimeError(f"Gitee 中找不到文件：{remote_path}")
        return RuntimeError(f"Gitee 返回的是目录列表，不是单个文件：{remote_path}")
    return RuntimeError(f"Gitee 返回了无法识别的数据格式：{type(response).__name__}")


def _response_as_file_object(response: Any, remote_path: str) -> dict:
    if isinstance(response, dict):
        return response
    raise _unexpected_response_error(response, remote_path)


def gitee_request(url: str, method: str, data: bytes | None = None) -> Any:
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "User-Agent": "GamesCloudSave"},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            raw = response.read()
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gitee API 错误 {exc.code}: {_extract_gitee_error(body)}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"网络连接失败：{exc.reason}") from exc


def get_remote_metadata(token: str, repo: str, json_path: str, branch: str) -> dict:
    response = _response_as_file_object(
        gitee_request(_contents_api_url(repo, json_path, branch, token), "GET"),
        json_path,
    )
    content = response.get("content", "")
    if not content:
        raise RuntimeError("云端说明文件缺少可读取的内容。")
    decoded = base64.b64decode(str(content).replace("\n", ""))
    return json.loads(decoded.decode("utf-8"))


def get_existing_file_sha(token: str, repo: str, remote_path: str, branch: str) -> str | None:
    try:
        response = gitee_request(_contents_api_url(repo, remote_path, branch, token), "GET")
        if isinstance(response, list) and not response:
            return None
        response = _response_as_file_object(response, remote_path)
        return response.get("sha")
    except RuntimeError as exc:
        if "404" in str(exc):
            return None
        raise


def upload_file_to_gitee(
    token: str,
    repo: str,
    remote_path: str,
    branch: str,
    encoded_content: str,
    commit_message: str,
    existing_sha: str | None,
) -> float:
    payload = {
        "access_token": token,
        "content": encoded_content,
        "message": commit_message,
        "branch": branch,
    }
    method = "POST"
    if existing_sha:
        payload["sha"] = existing_sha
        method = "PUT"
    raw = json.dumps(payload).encode("utf-8")
    started_at = time.monotonic()
    gitee_request(_contents_api_url(repo, remote_path, branch, token), method, data=raw)
    elapsed = max(time.monotonic() - started_at, 0.001)
    return len(raw) / elapsed


def _download_url_from_response(response: dict, token: str) -> str:
    download_url = str(response.get("download_url", "")).strip()
    if download_url:
        return download_url

    html_url = str(response.get("html_url", "")).strip()
    if "/blob/" in html_url:
        return html_url.replace("/blob/", "/raw/", 1)
    raise RuntimeError("Gitee 返回的数据里没有可下载的地址。")


def _write_base64_content_to_file(content: str, encoding: str, temp_path: str, emit_progress, status: str) -> bool:
    normalized_encoding = str(encoding).strip().lower()
    if not content or normalized_encoding != "base64":
        return False

    emit_progress(28, status)
    decoded = base64.b64decode(content)
    emit_progress(
        46,
        transfer_status(
            "正在写入下载的 zip 压缩包...",
            file_size=format_size(len(decoded)),
        ),
    )
    with open(temp_path, "wb") as target:
        target.write(decoded)
    return True


def _download_file_from_blob_api(token: str, repo: str, response: dict, temp_path: str, emit_progress) -> bool:
    sha = str(response.get("sha", "")).strip()
    if not sha:
        return False
    blob_response = gitee_request(_blob_api_url(repo, sha, token), "GET")
    blob_file = _response_as_file_object(blob_response, sha)
    content = str(blob_file.get("content", "")).replace("\n", "").strip()
    encoding = str(blob_file.get("encoding", "")).strip()
    return _write_base64_content_to_file(
        content,
        encoding,
        temp_path,
        emit_progress,
        "正在从 Gitee Blob API 读取完整文件内容...",
    )


def download_file(token: str, repo: str, remote_path: str, branch: str, emit_progress) -> str:
    response = _response_as_file_object(
        gitee_request(_contents_api_url(repo, remote_path, branch, token), "GET"),
        remote_path,
    )

    fd, temp_path = tempfile.mkstemp(prefix="games_save_download_", suffix=".zip")
    os.close(fd)

    url = _download_url_from_response(response, token)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "GamesCloudSave", "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response_handle, open(temp_path, "wb") as target:
            total = int(response_handle.headers.get("Content-Length", "0"))
            downloaded = 0
            started_at = time.monotonic()
            while True:
                chunk = response_handle.read(1024 * 256)
                if not chunk:
                    break
                target.write(chunk)
                downloaded += len(chunk)
                percent = 18 + (downloaded / total) * 38 if total > 0 else 18
                elapsed = max(time.monotonic() - started_at, 0.001)
                emit_progress(
                    percent,
                    transfer_status(
                        "正在下载云端 zip 压缩包...",
                        speed=format_speed(downloaded / elapsed),
                        file_size=format_size(total if total > 0 else downloaded),
                    ),
                )
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        if _download_file_from_blob_api(token, repo, response, temp_path, emit_progress):
            return temp_path
        if isinstance(exc, urllib.error.HTTPError):
            raise RuntimeError(f"下载失败：HTTP {exc.code}") from exc
        raise RuntimeError(f"下载失败，网络错误：{exc.reason}") from exc
    return temp_path
