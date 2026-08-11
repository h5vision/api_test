from __future__ import annotations

from backend.project_snapshots.adapters.github import GitHubAdapter as LegacyImportAdapter
from backend.integrations.github.project_snapshots.adapters.github import GitHubAdapter
from backend.snapshots.adapters.github import PublicGithubAdapter as LegacyPublicAdapter
from backend.integrations.github.snapshots.adapters.github import PublicGithubAdapter


def test_project_snapshot_github_adapter_moves_without_identity_change() -> None:
    assert LegacyImportAdapter is GitHubAdapter


def test_public_snapshot_github_adapter_moves_without_identity_change() -> None:
    assert LegacyPublicAdapter is PublicGithubAdapter
