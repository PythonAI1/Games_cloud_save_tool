import base64
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from constants import API_VERSION
from utils import format_size, format_speed, transfer_status


def metadata_path_for_zip(zip_path: str) -> str:
    zip_path = zip_path.strip()
    if zip_path.lower().endswith(".zip"):
        return zip_path[:-4] + ".json"
    return zip_path + ".json"


def github_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "GamesCloudSave",
    }


def contents_api_url(repo: str, remote_path: str, branch: str) -> str:
    quoted_path = urllib.parse.quote(remote_path.strip("/"), safe="/")
    query = urllib.parse.urlencode({"ref": branch})
    return f"https://api.github.com/repos/{repo}/contents/{quoted_path}?{query}"


def extract_github_error(body: str) -> str:
    try:
        data = json.loads(body)
        if isinstance(data, dict) and data.get("message"):
            return str(data["message"])
    except json.JSONDecodeError:
        pass
    return body.strip() or "未知错误"


def github_request(token: str, method: str, url: str, data: bytes | None = None) -> dict:
    request = urllib.request.Request(url, data=data, method=method, headers=github_headers(token))
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            raw = response.read()
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        message = extract_github_error(body)
        raise RuntimeError(f"GitHub API 错误 {exc.code}: {message}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"网络连接失败：{exc.reason}") from exc


def get_remote_metadata(token: str, repo: str, json_path: str, branch: str) -> dict:
    response = github_request(token, "GET", contents_api_url(repo, json_path, branch))
    content = response.get("content", "")
    encoding = response.get("encoding")
    if not content or encoding != "base64":
        raise RuntimeError("云端说明文件缺少可读取的内容。")
    decoded = base64.b64decode(content.replace("\n", ""))
    return json.loads(decoded.decode("utf-8"))


def get_download_url(token: str, repo: str, zip_path: str, branch: str) -> str:
    response = github_request(token, "GET", contents_api_url(repo, zip_path, branch))
    download_url = response.get("download_url")
    if not download_url:
        raise RuntimeError("GitHub 返回的数据里没有 download_url，无法下载备份。")
    return download_url


def get_existing_file_sha(token: str, repo: str, remote_path: str, branch: str) -> str | None:
    try:
        response = github_request(token, "GET", contents_api_url(repo, remote_path, branch))
        return response.get("sha")
    except RuntimeError as exc:
        if "404" in str(exc):
            return None
        raise


def put_file_to_github(
    token: str,
    repo: str,
    remote_path: str,
    branch: str,
    encoded_content: str,
    commit_message: str,
    existing_sha: str | None,
) -> float:
    payload = {
        "message": commit_message,
        "content": encoded_content,
        "branch": branch,
    }
    if existing_sha:
        payload["sha"] = existing_sha
    raw = json.dumps(payload).encode("utf-8")
    started_at = time.monotonic()
    github_request(token, "PUT", contents_api_url(repo, remote_path, branch), data=raw)
    elapsed = max(time.monotonic() - started_at, 0.001)
    return len(raw) / elapsed


def download_file(url: str, emit_progress) -> str:
    fd, temp_path = tempfile.mkstemp(prefix="games_save_download_", suffix=".zip")
    os.close(fd)

    request = urllib.request.Request(url, headers={"User-Agent": "GamesCloudSave"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response, open(temp_path, "wb") as target:
            total = int(response.headers.get("Content-Length", "0"))
            downloaded = 0
            started_at = time.monotonic()
            while True:
                chunk = response.read(1024 * 256)
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
