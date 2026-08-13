from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from .contracts import ExternalProjectSyncReport
from .repository import ExternalProjectRegistryError


class RagCatalogClientLike(Protocol):
    @property
    def base_url(self) -> str: ...

    def health(self) -> dict[str, Any]: ...

    def projects(self) -> list[dict[str, Any]]: ...


class RegistryLike(Protocol):
    def upsert_target(self, **kwargs: Any) -> Any: ...
    def mark_target_offline(self, target_id: str, error: str) -> None: ...
    def begin_observation(self, target_id: str) -> None: ...
    def upsert_external_project(self, *, target_id: str, value: dict[str, Any]) -> Any: ...
    def list_external_projects(self, target_id: str) -> list[dict[str, Any]]: ...
    def list_vision_projects(self) -> list[dict[str, Any]]: ...
    def list_bindings(self, target_id: str) -> list[dict[str, Any]]: ...
    def upsert_binding(self, **kwargs: Any) -> Any: ...


class ExternalProjectRegistryService:
    """Reconcile RAG Lab observations with canonical Vision project identities.

    Catalog synchronization is deliberately separate from Chat-time revision
    verification. This service persists what was observed and stable identity
    relationships; it does not authorize a Chat request to use an observed revision.
    """

    def __init__(
        self,
        registry: RegistryLike,
        rag_client: RagCatalogClientLike,
        *,
        target_id: str = "rag-lab-main",
        target_name: str = "RAG Lab",
    ) -> None:
        self._registry = registry
        self._rag = rag_client
        self._target_id = target_id.strip() or "rag-lab-main"
        self._target_name = target_name.strip() or "RAG Lab"

    def sync(self) -> ExternalProjectSyncReport:
        base_url = str(self._rag.base_url or "").strip().rstrip("/")
        self._registry.upsert_target(
            target_id=self._target_id,
            name=self._target_name,
            base_url=base_url,
            enabled=True,
            availability="unknown",
            error=None,
        )
        try:
            self._rag.health()
            projects = [item for item in self._rag.projects() if isinstance(item, dict)]
        except Exception as exc:
            detail = str(exc)[:2000] or exc.__class__.__name__
            self._registry.mark_target_offline(self._target_id, detail)
            return ExternalProjectSyncReport(
                target_id=self._target_id,
                observed_projects=0,
                verified_bindings=0,
                candidate_bindings=0,
                ambiguous_projects=0,
                unbound_projects=0,
                stale_projects=len(self._registry.list_external_projects(self._target_id)),
                availability="offline",
                error=detail,
            )

        self._registry.begin_observation(self._target_id)
        for item in projects:
            self._registry.upsert_external_project(target_id=self._target_id, value=item)
        self._registry.upsert_target(
            target_id=self._target_id,
            name=self._target_name,
            base_url=base_url,
            enabled=True,
            availability="online",
            error=None,
        )

        external = self._registry.list_external_projects(self._target_id)
        active_external = [
            item for item in external if str(item.get("availability") or "") == "online"
        ]
        vision = self._registry.list_vision_projects()
        existing = {
            str(item.get("project_id") or ""): item
            for item in self._registry.list_bindings(self._target_id)
        }

        verified = 0
        candidates = 0
        ambiguous = 0
        unbound = 0
        for project in vision:
            project_id = str(project.get("project_id") or "").strip()
            if not project_id:
                continue
            prior = existing.get(project_id)
            if prior and str(prior.get("binding_method") or "") == "manual":
                verified += int(str(prior.get("verification_state") or "") == "verified")
                continue

            decision = _binding_decision(project, active_external)
            if decision is None:
                unbound += 1
                continue
            if decision[0] == "ambiguous":
                ambiguous += 1
                continue
            external_project_id, method, strength, state = decision
            self._registry.upsert_binding(
                project_id=project_id,
                target_id=self._target_id,
                external_project_id=external_project_id,
                binding_method=method,
                binding_strength=strength,
                verification_state=state,
                error=None,
                preserve_manual=True,
            )
            if state == "verified":
                verified += 1
            elif state == "candidate":
                candidates += 1

        stale = sum(
            1 for item in external if str(item.get("availability") or "") == "stale"
        )
        return ExternalProjectSyncReport(
            target_id=self._target_id,
            observed_projects=len(projects),
            verified_bindings=verified,
            candidate_bindings=candidates,
            ambiguous_projects=ambiguous,
            unbound_projects=unbound,
            stale_projects=stale,
            availability="online",
            error=None,
        )


def _binding_decision(
    project: dict[str, Any],
    external: Iterable[dict[str, Any]],
) -> tuple[str, str, str, str] | None:
    rows = list(external)
    project_id = str(project.get("project_id") or "").strip()
    revision = str(
        project.get("snapshot_revision") or project.get("git_commit_sha") or ""
    ).strip().casefold()

    if revision:
        matches = [
            row
            for row in rows
            if str(row.get("revision") or "").strip().casefold() == revision
        ]
        if len(matches) == 1:
            return (
                str(matches[0]["external_project_id"]),
                "revision_exact",
                "revision_exact",
                "verified",
            )
        if len(matches) > 1:
            return ("ambiguous", "revision_exact", "ambiguous", "conflict")

    exact = [
        row
        for row in rows
        if str(row.get("external_project_id") or "").strip().casefold()
        == project_id.casefold()
    ]
    if len(exact) == 1:
        return (
            str(exact[0]["external_project_id"]),
            "project_id_exact",
            "project_id_exact",
            "verified",
        )
    if len(exact) > 1:
        return ("ambiguous", "project_id_exact", "ambiguous", "conflict")

    leaf = project_id.rsplit("/", 1)[-1].casefold()
    leaf_matches = [
        row
        for row in rows
        if str(row.get("external_project_id") or "")
        .strip()
        .rsplit("/", 1)[-1]
        .casefold()
        == leaf
    ]
    if len(leaf_matches) == 1:
        return (
            str(leaf_matches[0]["external_project_id"]),
            "leaf_candidate",
            "leaf_unique",
            "candidate",
        )
    if len(leaf_matches) > 1:
        return ("ambiguous", "leaf_candidate", "ambiguous", "conflict")
    return None
