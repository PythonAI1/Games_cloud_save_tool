from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable


ProgressCallback = Callable[[float, str], None]


def metadata_path_for_zip(zip_path: str) -> str:
    zip_path = zip_path.strip()
    if zip_path.lower().endswith(".zip"):
        return zip_path[:-4] + ".json"
    return zip_path + ".json"


class StorageProvider(ABC):
    provider_type = ""
    display_name = ""

    @abstractmethod
    def get_remote_metadata(self, token: str, repo: str, json_path: str, branch: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    def get_existing_file_sha(self, token: str, repo: str, remote_path: str, branch: str) -> str | None:
        raise NotImplementedError

    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
    def download_file(self, token: str, repo: str, remote_path: str, branch: str, emit_progress: ProgressCallback) -> str:
        raise NotImplementedError
