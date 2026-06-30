from __future__ import annotations

import gitee_client

from .base import ProgressCallback, StorageProvider


class GiteeProvider(StorageProvider):
    provider_type = "gitee"
    display_name = "Gitee"

    def get_remote_metadata(self, token: str, repo: str, json_path: str, branch: str) -> dict:
        return gitee_client.get_remote_metadata(token, repo, json_path, branch)

    def get_existing_file_sha(self, token: str, repo: str, remote_path: str, branch: str) -> str | None:
        return gitee_client.get_existing_file_sha(token, repo, remote_path, branch)

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
        return gitee_client.upload_file_to_gitee(
            token,
            repo,
            remote_path,
            branch,
            encoded_content,
            commit_message,
            existing_sha,
        )

    def download_file(self, token: str, repo: str, remote_path: str, branch: str, emit_progress: ProgressCallback) -> str:
        return gitee_client.download_file(token, repo, remote_path, branch, emit_progress)
