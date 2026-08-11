"""Compatibility import path for the public GitHub Snapshot adapter."""

from ...integrations.github.snapshots.adapters.github import (
    GithubAdapterError,
    PublicGithubAdapter,
)

__all__ = ["GithubAdapterError", "PublicGithubAdapter"]
