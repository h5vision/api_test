from __future__ import annotations


import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


from ..contracts import (
    TreeEntry,
    normalize_git_sha,
    normalize_repository_full_name,
    normalize_repository_path,
)
from .base import GithubCommitInfo, GithubFile, GithubRepositoryInfo




class GithubAdapterError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code




class PublicGithubAdapter:
    """Read-only GitHub adapter for public repositories.


    This MVP intentionally sends no Authorization header. Private repository support
    must be added later through a repository-scoped GitHub App credential binding.
    """


    def __init__(
        self,
        *,
        api_base_url: str = "https://api.github.com",
        timeout_seconds: int = 20,
        max_response_bytes: int = 16 * 1024 * 1024,
        max_file_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        self._api_base_url = api_base_url.rstrip("/")
        self._timeout_seconds = max(1, timeout_seconds)
        self._max_response_bytes = max(1024, max_response_bytes)
        self._max_file_bytes = max(1024, max_file_bytes)


    def _request_json(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self._api_base_url}{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Vision-Snapshot-MVP/1.0",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self._timeout_seconds,
            ) as response:
                body = response.read(self._max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise GithubAdapterError(
                    "GitHub repository, ref, tree, or file was not found",
                    status_code=404,
                ) from exc
            if exc.code in {401, 403}:
                raise GithubAdapterError(
                    "GitHub rejected the anonymous public-repository request",
                    status_code=502,
                ) from exc
            raise GithubAdapterError(
                f"GitHub API returned HTTP {exc.code}",
                status_code=502,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise GithubAdapterError(
                f"GitHub API is unavailable: {exc}",
                status_code=503,
            ) from exc


        if len(body) > self._max_response_bytes:
            raise GithubAdapterError("GitHub response exceeded the configured limit")
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GithubAdapterError("GitHub returned an invalid JSON response") from exc
        if not isinstance(value, dict):
            raise GithubAdapterError("GitHub returned an invalid JSON object")
        return value


    def get_repository(self, repository_full_name: str) -> GithubRepositoryInfo:
        full_name = normalize_repository_full_name(repository_full_name)
        encoded_name = urllib.parse.quote(full_name, safe="/")
        value = self._request_json(f"/repos/{encoded_name}")


        returned_name = normalize_repository_full_name(str(value.get("full_name") or ""))
        if returned_name.casefold() != full_name.casefold():
            raise GithubAdapterError("GitHub repository identity did not match the request")


        private = bool(value.get("private"))
        visibility = str(value.get("visibility") or "public").lower()
        if private or visibility != "public":
            raise GithubAdapterError(
                "The public GitHub Snapshot MVP does not support private repositories",
                status_code=403,
            )


        provider_repository_id = str(value.get("id") or "").strip()
        default_branch = str(value.get("default_branch") or "").strip()
        clone_url = str(value.get("clone_url") or "").strip()
        if not provider_repository_id or not default_branch or not clone_url:
            raise GithubAdapterError("GitHub repository metadata was incomplete")


        return GithubRepositoryInfo(
            provider_repository_id=provider_repository_id,
            repository_full_name=returned_name,
            clone_url=clone_url,
            default_branch=default_branch,
            private=False,
        )


    def resolve_commit(self, repository_full_name: str, ref: str) -> GithubCommitInfo:
        full_name = normalize_repository_full_name(repository_full_name)
        normalized_ref = ref.strip()
        if not normalized_ref or "\x00" in normalized_ref:
            raise GithubAdapterError("GitHub ref must not be blank", status_code=422)


        encoded_name = urllib.parse.quote(full_name, safe="/")
        encoded_ref = urllib.parse.quote(normalized_ref, safe="")
        value = self._request_json(f"/repos/{encoded_name}/commits/{encoded_ref}")


        commit_sha = normalize_git_sha(str(value.get("sha") or ""))
        commit_object = value.get("commit")
        if not isinstance(commit_object, dict):
            raise GithubAdapterError("GitHub commit metadata was incomplete")
        tree_object = commit_object.get("tree")
        if not isinstance(tree_object, dict):
            raise GithubAdapterError("GitHub commit tree metadata was incomplete")
        tree_sha = normalize_git_sha(str(tree_object.get("sha") or ""))
        return GithubCommitInfo(commit_sha=commit_sha, tree_sha=tree_sha)


    def verify_commit_tree(
        self,
        repository_full_name: str,
        commit_sha: str,
        expected_tree_sha: str,
    ) -> None:
        resolved = self.resolve_commit(repository_full_name, normalize_git_sha(commit_sha))
        expected = normalize_git_sha(expected_tree_sha)
        if resolved.commit_sha != normalize_git_sha(commit_sha):
            raise GithubAdapterError("GitHub commit identity changed unexpectedly")
        if resolved.tree_sha != expected:
            raise GithubAdapterError(
                "Stored commit and tree SHA do not describe the same Git state",
                status_code=409,
            )


    def get_tree(self, repository_full_name: str, tree_sha: str) -> list[TreeEntry]:
        full_name = normalize_repository_full_name(repository_full_name)
        normalized_tree_sha = normalize_git_sha(tree_sha)
        encoded_name = urllib.parse.quote(full_name, safe="/")
        value = self._request_json(
            f"/repos/{encoded_name}/git/trees/{normalized_tree_sha}?recursive=1"
        )
        returned_sha = normalize_git_sha(str(value.get("sha") or ""))
        if returned_sha != normalized_tree_sha:
            raise GithubAdapterError("GitHub returned a different tree object")
        if bool(value.get("truncated")):
            raise GithubAdapterError(
                "GitHub truncated the recursive tree; pagination is required",
                status_code=409,
            )


        raw_entries = value.get("tree")
        if not isinstance(raw_entries, list):
            raise GithubAdapterError("GitHub tree response had no tree array")


        entries: list[TreeEntry] = []
        for item in raw_entries:
            if not isinstance(item, dict):
                continue
            entry_type = str(item.get("type") or "")
            if entry_type not in {"blob", "tree"}:
                continue
            path = normalize_repository_path(str(item.get("path") or ""))
            object_sha = normalize_git_sha(str(item.get("sha") or ""))
            size_value = item.get("size")
            size = int(size_value) if size_value is not None else None
            entries.append(
                TreeEntry(
                    path=path,
                    entry_type=entry_type,
                    object_sha=object_sha,
                    size=size,
                    mode=str(item.get("mode") or ""),
                )
            )
        entries.sort(key=lambda item: (item.path.casefold(), item.path))
        return entries


    def get_file(
        self,
        repository_full_name: str,
        commit_sha: str,
        path: str,
    ) -> GithubFile:
        full_name = normalize_repository_full_name(repository_full_name)
        normalized_commit_sha = normalize_git_sha(commit_sha)
        normalized_path = normalize_repository_path(path)
        encoded_name = urllib.parse.quote(full_name, safe="/")
        encoded_path = urllib.parse.quote(normalized_path, safe="/")
        query = urllib.parse.urlencode({"ref": normalized_commit_sha})
        value = self._request_json(
            f"/repos/{encoded_name}/contents/{encoded_path}?{query}"
        )


        if str(value.get("type") or "") != "file":
            raise GithubAdapterError("The requested repository path is not a file", status_code=404)
        returned_path = normalize_repository_path(str(value.get("path") or ""))
        if returned_path != normalized_path:
            raise GithubAdapterError("GitHub returned a different repository path")


        size = int(value.get("size") or 0)
        if size < 0 or size > self._max_file_bytes:
            raise GithubAdapterError(
                "The requested file exceeds the Snapshot MVP file-size limit",
                status_code=413,
            )
        if str(value.get("encoding") or "") != "base64":
            raise GithubAdapterError("GitHub did not return inline base64 file content")
        raw_content = value.get("content")
        if not isinstance(raw_content, str):
            raise GithubAdapterError("GitHub file response had no content")
        try:
            decoded = base64.b64decode(raw_content, validate=False)
        except (ValueError, TypeError) as exc:
            raise GithubAdapterError("GitHub returned invalid base64 file content") from exc
        if len(decoded) != size or len(decoded) > self._max_file_bytes:
            raise GithubAdapterError("GitHub file size did not match the decoded content")
        try:
            content = decoded.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GithubAdapterError(
                "The Snapshot MVP only returns UTF-8 text files",
                status_code=415,
            ) from exc


        return GithubFile(
            path=returned_path,
            blob_sha=normalize_git_sha(str(value.get("sha") or "")),
            size=size,
            content=content,
        )