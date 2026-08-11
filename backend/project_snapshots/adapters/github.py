"""Compatibility import path for the GitHub Snapshot-import adapter."""

from ...integrations.github.project_snapshots.adapters.github import (
    GitHubAdapter,
    GitHubAdapterError,
    GitHubCommitInfo,
    GitHubRepositoryInfo,
)

__all__ = [
    "GitHubAdapter",
    "GitHubAdapterError",
    "GitHubCommitInfo",
    "GitHubRepositoryInfo",
]
