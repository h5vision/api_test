from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from ..contracts import normalize_git_sha, normalize_repository_full_name


class GitHubAdapterError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class GitHubRepositoryInfo:
    provider_repository_id: str
    repository_full_name: str
    clone_url: str
    default_branch: str
    private: bool


@dataclass(frozen=True)
class GitHubCommitInfo:
    commit_sha: str
    tree_sha: str


class GitHubAdapter:
    """Read public GitHub metadata using an optional server-side token.

    The token is never returned to the Admin UI and is only attached to outbound
    GitHub API requests. Anonymous access remains supported as a fallback.
    """

    def __init__(
        self,
        *,
        token: str = "",
        api_base_url: str = "https://api.github.com",
        timeout_seconds: int = 20,
        max_response_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        self._token = token.strip()
        self._api_base_url = api_base_url.rstrip("/")
        self._timeout_seconds = max(1, timeout_seconds)
        self._max_response_bytes = max(1024, max_response_bytes)

    @property
    def authenticated(self) -> bool:
        return bool(self._token)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "Vision-Snapshot-Control/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request_json(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self._api_base_url}{path}",
            headers=self._headers(),
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                body = response.read(self._max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            remaining = (exc.headers.get("X-RateLimit-Remaining") or "").strip()
            if exc.code == 404:
                raise GitHubAdapterError("GitHub repository or ref was not found", status_code=404) from exc
            if exc.code in {401, 403}:
                if remaining == "0":
                    raise GitHubAdapterError(
                        "GitHub API rate limit was exhausted; configure or refresh the server-side Snapshot token",
                        status_code=429,
                    ) from exc
                raise GitHubAdapterError(
                    "GitHub rejected the Snapshot metadata request",
                    status_code=502,
                ) from exc
            raise GitHubAdapterError(f"GitHub API returned HTTP {exc.code}", status_code=502) from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise GitHubAdapterError("GitHub API is unavailable", status_code=503) from exc
        if len(body) > self._max_response_bytes:
            raise GitHubAdapterError("GitHub response exceeded the configured limit")
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubAdapterError("GitHub returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise GitHubAdapterError("GitHub returned an invalid response object")
        return value

    def get_repository(self, repository_full_name: str) -> GitHubRepositoryInfo:
        full_name = normalize_repository_full_name(repository_full_name)
        encoded = urllib.parse.quote(full_name, safe="/")
        value = self._request_json(f"/repos/{encoded}")
        returned_name = normalize_repository_full_name(str(value.get("full_name") or ""))
        if returned_name.casefold() != full_name.casefold():
            raise GitHubAdapterError("GitHub repository identity did not match the request", status_code=409)
        private = bool(value.get("private"))
        visibility = str(value.get("visibility") or "public").lower()
        if private or visibility != "public":
            raise GitHubAdapterError("Private repositories are not enabled for Snapshot import", status_code=403)
        provider_repository_id = str(value.get("id") or "").strip()
        default_branch = str(value.get("default_branch") or "").strip()
        clone_url = str(value.get("clone_url") or "").strip()
        if not provider_repository_id or not default_branch or not clone_url:
            raise GitHubAdapterError("GitHub repository metadata was incomplete")
        return GitHubRepositoryInfo(
            provider_repository_id=provider_repository_id,
            repository_full_name=returned_name,
            clone_url=clone_url,
            default_branch=default_branch,
            private=False,
        )

    def resolve_commit(self, repository_full_name: str, ref: str) -> GitHubCommitInfo:
        full_name = normalize_repository_full_name(repository_full_name)
        normalized_ref = ref.strip()
        if not normalized_ref or "\x00" in normalized_ref:
            raise GitHubAdapterError("GitHub ref must not be blank", status_code=422)
        encoded_name = urllib.parse.quote(full_name, safe="/")
        encoded_ref = urllib.parse.quote(normalized_ref, safe="")
        value = self._request_json(f"/repos/{encoded_name}/commits/{encoded_ref}")
        commit_sha = normalize_git_sha(str(value.get("sha") or ""))
        commit_object = value.get("commit")
        if not isinstance(commit_object, dict) or not isinstance(commit_object.get("tree"), dict):
            raise GitHubAdapterError("GitHub commit metadata was incomplete")
        tree_sha = normalize_git_sha(str(commit_object["tree"].get("sha") or ""))
        return GitHubCommitInfo(commit_sha=commit_sha, tree_sha=tree_sha)

