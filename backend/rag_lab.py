from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .schemas import Source


class RagLabError(RuntimeError):
    def __init__(self, message: str, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class RagLabSearchResult:
    has_evidence: bool
    sources: list[Source]
    top_score: float
    threshold: float
    reason: str


@dataclass(frozen=True)
class RagLabPromptResult(RagLabSearchResult):
    messages: list[dict[str, str]]
    provenance: dict[str, Any]


@dataclass(frozen=True)
class RagLabProjectBinding:
    requested_project_id: str
    external_project_id: str
    binding_strength: str
    verification_state: str
    revision: str | None
    indexed_at: str | None
    fingerprint: dict[str, Any]


class RagLabClient:
    """HTTP adapter for the rag_lab contract owned by the VectorDB team."""

    def __init__(
        self,
        base_url: str,
        token: str = "",
        timeout_seconds: int = 60,
        base_url_provider: Callable[[], str] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout_seconds = max(1, timeout_seconds)
        self._base_url_provider = base_url_provider

    @property
    def base_url(self) -> str:
        if self._base_url_provider is not None:
            resolved = self._base_url_provider().strip().rstrip("/")
            if resolved:
                return resolved
        return self._base_url

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        if not self.base_url:
            raise RagLabError("rag_lab base URL is not configured", 503)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "VisionBackend/1.0",
        }
        if self._token:
            headers["X-VSS-Token"] = self._token
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=(
                json.dumps(payload, ensure_ascii=False).encode("utf-8")
                if payload is not None
                else None
            ),
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout_seconds or self._timeout_seconds,
            ) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = self._error_detail(exc)
            raise RagLabError(
                f"rag_lab {method} {path} failed: {detail}",
                status_code=exc.code,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise RagLabError(
                f"rag_lab server is unavailable at {self.base_url}: {exc}",
                status_code=503,
            ) from exc
        try:
            value = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RagLabError(
                f"rag_lab returned a non-JSON response for {path}",
                status_code=502,
            ) from exc
        if not isinstance(value, dict):
            raise RagLabError(
                f"rag_lab returned an invalid JSON object for {path}",
                status_code=502,
            )
        return value

    @staticmethod
    def _error_detail(exc: urllib.error.HTTPError) -> str:
        try:
            value = json.loads(exc.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            return f"HTTP {exc.code}"
        if isinstance(value, dict):
            detail = value.get("detail") or value.get("error") or value.get("reason")
            if detail:
                return str(detail)
        return f"HTTP {exc.code}"

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health", timeout_seconds=10)

    def projects(self) -> list[dict[str, Any]]:
        value = self._request("GET", "/projects", timeout_seconds=10)
        projects = value.get("projects")
        if not isinstance(projects, list):
            raise RagLabError("rag_lab /projects response has no projects array", 502)
        return [item for item in projects if isinstance(item, dict)]

    def index_exists(self, project_id: str) -> dict[str, Any]:
        query = urllib.parse.urlencode({"project_id": project_id})
        return self._request("GET", f"/index/exists?{query}", timeout_seconds=10)

    def briefing(self, project_id: str) -> dict[str, Any]:
        query = urllib.parse.urlencode({"project_id": project_id})
        value = self._request("GET", f"/briefing?{query}", timeout_seconds=15)
        if str(value.get("project_id") or "").strip() != project_id.strip():
            raise RagLabError("rag_lab /briefing returned a mismatched project_id", 502)
        if not isinstance(value.get("briefing"), str) or not value["briefing"].strip():
            raise RagLabError("rag_lab /briefing response has no briefing text", 502)
        for key in ("references", "reference_files", "mentioned_files"):
            if value.get(key) is not None and not isinstance(value[key], list):
                raise RagLabError(f"rag_lab /briefing response has invalid {key}", 502)
        if value.get("structure") is not None and not isinstance(value["structure"], dict):
            raise RagLabError("rag_lab /briefing response has invalid structure", 502)
        return value

    def resolve_project(
        self,
        project_id: str,
        *,
        revision: str | None = None,
    ) -> RagLabProjectBinding:
        """Map Vision identity to an external project without guessing ambiguously."""

        requested = project_id.strip()
        requested_leaf = requested.rsplit("/", 1)[-1].casefold()
        normalized_revision = (revision or "").strip().casefold()
        ready = [
            item
            for item in self.projects()
            if str(item.get("state") or "done").casefold() == "done"
            and str(item.get("project_id") or "").strip()
        ]
        revision_matches = [
            item
            for item in ready
            if normalized_revision
            and str(item.get("commit") or "").strip().casefold()
            == normalized_revision
        ]
        exact_matches = [
            item
            for item in ready
            if str(item.get("project_id") or "").strip().casefold()
            == requested.casefold()
        ]
        leaf_matches = [
            item
            for item in ready
            if str(item.get("project_id") or "").strip().casefold()
            == requested_leaf
        ]

        selected: dict[str, Any] | None = None
        strength = "project_only"
        verification = "unverified"
        if len(revision_matches) == 1:
            selected = revision_matches[0]
        elif len(exact_matches) == 1:
            selected = exact_matches[0]
        elif len(leaf_matches) == 1:
            selected = leaf_matches[0]
        if selected is not None and normalized_revision:
            external_revision = str(selected.get("commit") or "").strip().casefold()
            if external_revision == normalized_revision:
                strength = "revision_matched"
                verification = "compatible"
            elif exact_matches or leaf_matches:
                raise RagLabError(
                    "external VectorDB project revision does not match the active Snapshot",
                    409,
                )
        if selected is None:
            if len(revision_matches) > 1:
                raise RagLabError(
                    "external VectorDB project mapping is ambiguous for the Snapshot revision",
                    409,
                )
            raise RagLabError(
                f"external VectorDB has no compatible project for {requested}",
                409,
            )
        return RagLabProjectBinding(
            requested_project_id=requested,
            external_project_id=str(selected["project_id"]).strip(),
            binding_strength=strength,
            verification_state=verification,
            revision=(
                str(selected["commit"]).strip()
                if selected.get("commit")
                else None
            ),
            indexed_at=(
                str(selected["indexed_at"]).strip()
                if selected.get("indexed_at")
                else None
            ),
            fingerprint=(
                dict(selected["fingerprint"])
                if isinstance(selected.get("fingerprint"), dict)
                else {}
            ),
        )

    def search(
        self,
        project_id: str,
        query: str,
        *,
        top_k: int | None = None,
        threshold: float | None = None,
    ) -> RagLabSearchResult:
        payload: dict[str, Any] = {"project_id": project_id, "query": query}
        if top_k is not None:
            payload["top_k"] = top_k
        if threshold is not None:
            payload["threshold"] = threshold
        value = self._request("POST", "/search", payload)
        return self._search_result(project_id, value)

    def prompt(
        self,
        project_id: str,
        query: str,
    ) -> RagLabPromptResult:
        value = self._request(
            "POST",
            "/prompt",
            {"project_id": project_id, "query": query},
        )
        base = self._search_result(project_id, value, source_key="sources")
        raw_messages = value.get("messages")
        if not isinstance(raw_messages, list):
            raise RagLabError("rag_lab /prompt response has no messages array", 502)
        messages: list[dict[str, str]] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                raise RagLabError("rag_lab /prompt returned an invalid message", 502)
            role = item.get("role")
            content = item.get("content")
            if role not in {"system", "user", "assistant"} or not isinstance(content, str):
                raise RagLabError("rag_lab /prompt returned an invalid message", 502)
            messages.append({"role": role, "content": content})
        if base.has_evidence and not messages:
            raise RagLabError("rag_lab /prompt returned empty messages", 502)
        return RagLabPromptResult(
            has_evidence=base.has_evidence,
            sources=base.sources,
            top_score=base.top_score,
            threshold=base.threshold,
            reason=base.reason,
            messages=messages,
            provenance={
                key: value.get(key)
                for key in (
                    "project_id",
                    "snapshot_id",
                    "index_id",
                    "index_version",
                    "commit",
                    "timing",
                    "stage",
                )
                if value.get(key) is not None
            },
        )

    def _search_result(
        self,
        project_id: str,
        value: dict[str, Any],
        *,
        source_key: str = "contexts",
    ) -> RagLabSearchResult:
        raw_sources = value.get(source_key)
        if not isinstance(raw_sources, list):
            raise RagLabError(
                f"rag_lab response has no {source_key} array",
                status_code=502,
            )
        sources = [
            self._source(project_id, item, index)
            for index, item in enumerate(raw_sources, start=1)
            if isinstance(item, dict)
        ]
        return RagLabSearchResult(
            has_evidence=bool(value.get("has_evidence")),
            sources=sources,
            top_score=float(value.get("top_score") or 0.0),
            threshold=float(value.get("threshold") or 0.0),
            reason=str(value.get("reason") or ("ok" if sources else "no_evidence")),
        )

    @staticmethod
    def _source(project_id: str, item: dict[str, Any], citation_id: int) -> Source:
        path = str(item.get("path") or "").strip() or None
        text = str(item.get("text") or "")
        line_start = RagLabClient._optional_int(item.get("line_start"))
        line_end = RagLabClient._optional_int(item.get("line_end"))
        identity = "\n".join(
            [project_id, path or "", str(line_start or ""), str(line_end or ""), text]
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        document_id = str(item.get("document_id") or f"ragdoc_{digest[:24]}")
        chunk_id = str(item.get("chunk_id") or f"ragchunk_{digest[:32]}")
        metadata = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}
        if item.get("_id") is not None:
            metadata["external_source_id"] = str(item["_id"])
        for key in ("type", "section"):
            if item.get(key) is not None:
                metadata[key] = item[key]
        metadata["rag_provider"] = "rag_lab"
        return Source(
            citation_id=citation_id,
            document_id=document_id,
            chunk_id=chunk_id,
            path=path,
            language=(str(item["language"]) if item.get("language") else None),
            line_start=line_start,
            line_end=line_end,
            text=text,
            score=float(item.get("score") or 0.0),
            metadata=metadata,
        )

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
