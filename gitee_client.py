import base64
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from utils import format_size, format_speed, transfer_status


def _repo_parts(repo: str) -> tuple[str, str]:
    owner, name = repo.strip().split("/", 1)
    return owner.strip(), name.strip()


def _contents_api_url(repo: str, remote_path: str, branch: str, token: str) -> str:
    owner, name = _repo_parts(repo)
    quoted_path = urllib.parse.quote(remote_path.strip("/"), safe="/")
    query = urllib.parse.urlencode({"access_token": token, "ref": branch})
    return f"https://gitee.com/api/v5/repos/{owner}/{name}/contents/{quoted_path}?{query}"


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


def gitee_request(url: str, method: str, data: bytes | None = None) -> dict:
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
    response = gitee_request(_contents_api_url(repo, json_path, branch, token), "GET")
    content = response.get("content", "")
    if not content:
        raise RuntimeError("云端说明文件缺少可读取的内容。")
    decoded = base64.b64decode(str(content).replace("\n", ""))
    return json.loads(decoded.decode("utf-8"))


def get_existing_file_sha(token: str, repo: str, remote_path: str, branch: str) -> str | None:
    try:
        response = gitee_request(_contents_api_url(repo, remote_path, branch, token), "GET")
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
        separator = "&" if "?" in download_url else "?"
        return f"{download_url}{separator}access_token={urllib.parse.quote(token)}"

    html_url = str(response.get("html_url", "")).strip()
    if "/blob/" in html_url:
        return html_url.replace("/blob/", "/raw/", 1)
    raise RuntimeError("Gitee 返回的数据里没有可下载的地址。")


def download_file(token: str, repo: str, remote_path: str, branch: str, emit_progress) -> str:
    response = gitee_request(_contents_api_url(repo, remote_path, branch, token), "GET")
    url = _download_url_from_response(response, token)

    fd, temp_path = tempfile.mkstemp(prefix="games_save_download_", suffix=".zip")
    os.close(fd)

    request = urllib.request.Request(url, headers={"User-Agent": "GamesCloudSave"})
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
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"下载失败：HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"下载失败，网络错误：{exc.reason}") from exc
    return temp_path
