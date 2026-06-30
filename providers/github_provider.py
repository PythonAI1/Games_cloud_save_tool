from __future__ import annotations

from github_client import download_file, get_download_url, get_existing_file_sha, get_remote_metadata, put_file_to_github

from .base import ProgressCallback, StorageProvider


class GitHubProvider(StorageProvider):
    provider_type = "github"
    display_name = "GitHub"

    def get_remote_metadata(self, token: str, repo: str, json_path: str, branch: str) -> dict:
        return get_remote_metadata(token, repo, json_path, branch)

    def get_existing_file_sha(self, token: str, repo: str, remote_path: str, branch: str) -> str | None:
        return get_existing_file_sha(token, repo, remote_path, branch)

    def upload_file(
        self,
        token: str,
        repo: str,
        remote_path: str,
        branch: str,
        encoded_content: str,
        commit_message: str,
        existing_sha: str | None,
    ) -> float:
        return put_file_to_github(token, repo, remote_path, branch, encoded_content, commit_message, existing_sha)

    def download_file(self, token: str, repo: str, remote_path: str, branch: str, emit_progress: ProgressCallback) -> str:
        return download_file(get_download_url(token, repo, remote_path, branch), emit_progress)
