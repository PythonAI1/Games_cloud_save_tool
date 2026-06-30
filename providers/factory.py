from __future__ import annotations

from .base import StorageProvider
from .gitee_provider import GiteeProvider
from .github_provider import GitHubProvider


PROVIDER_TYPES = {
    "github": GitHubProvider,
    "gitee": GiteeProvider,
}


def split_repo_provider(repo: str) -> tuple[str | None, str]:
    value = str(repo or "").strip()
    if ":" not in value:
        return None, value
    prefix, rest = value.split(":", 1)
    prefix = prefix.strip().lower()
    rest = rest.strip()
    if prefix in PROVIDER_TYPES and rest:
        return prefix, rest
    return None, value


def provider_type_from_config(config_data: dict) -> str:
    repo_provider, _repo = split_repo_provider(str(config_data.get("repo", "")))
    if repo_provider in PROVIDER_TYPES:
        return repo_provider
    provider_type = str(config_data.get("provider_type", "github")).strip().lower()
    if provider_type in PROVIDER_TYPES:
        return provider_type
    if provider_type not in PROVIDER_TYPES:
        return "github"
    return provider_type


def normalized_repo_name(config_data: dict) -> str:
    _provider_type, repo = split_repo_provider(str(config_data.get("repo", "")))
    return repo


def provider_display_name(provider_type: str) -> str:
    provider_class = PROVIDER_TYPES.get(provider_type, GitHubProvider)
    return provider_class.display_name


def create_provider(config_data: dict) -> StorageProvider:
    provider_type = provider_type_from_config(config_data)
    provider_class = PROVIDER_TYPES.get(provider_type, GitHubProvider)
    return provider_class()
