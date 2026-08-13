from __future__ import annotations


from dataclasses import dataclass
from typing import Any, Protocol


from ..contracts import TreeEntry




@dataclass(frozen=True)
class GithubRepositoryInfo:
    provider_repository_id: str
    repository_full_name: str
    clone_url: str
    default_branch: str
    private: bool




@dataclass(frozen=True)
class GithubCommitInfo:
    commit_sha: str
    tree_sha: str




@dataclass(frozen=True)
class GithubFile:
    path: str
    blob_sha: str
    size: int
    content: str




class GithubSourceAdapter(Protocol):
    def get_repository(self, repository_full_name: str) -> GithubRepositoryInfo:
        ...


    def resolve_commit(self, repository_full_name: str, ref: str) -> GithubCommitInfo:
        ...


    def compare_commits(
        self,
        repository_full_name: str,
        base_sha: str,
        target_sha: str,
    ) -> dict[str, Any]:
        ...


    def verify_commit_tree(
        self,
        repository_full_name: str,
        commit_sha: str,
        expected_tree_sha: str,
    ) -> None:
        ...


    def get_tree(self, repository_full_name: str, tree_sha: str) -> list[TreeEntry]:
        ...


    def get_file(
        self,
        repository_full_name: str,
        commit_sha: str,
        path: str,
    ) -> GithubFile:
        ...