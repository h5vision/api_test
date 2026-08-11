from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from .config import Settings
from .schema_guard import SchemaStateError, require_schema


RoutingMode = Literal["managed_auto", "pinned"]


class ProjectVectorRouteStoreError(RuntimeError):
    pass


class ProjectVectorRouteConflict(ProjectVectorRouteStoreError):
    pass


@dataclass(frozen=True)
class ProjectVectorRouteRecord:
    project_id: str
    tenant_id: str
    active_binding_id: str | None
    routing_mode: RoutingMode
    revision: int
    selected_by: str | None
    selected_at: datetime | None
    reason: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ProjectVectorRouteEventRecord:
    event_id: str
    project_id: str
    tenant_id: str
    from_binding_id: str | None
    to_binding_id: str | None
    routing_mode: RoutingMode
    actor: str | None
    reason: str | None
    revision: int
    created_at: datetime


@dataclass(frozen=True)
class RouteCandidateContext:
    binding_id: str
    tenant_id: str
    snapshot_id: str
    snapshot_project_id: str
    snapshot_status: str
    snapshot_tenant_id: str
    generation_id: str | None
    generation_status: str | None
    vector_index_id: str
    vector_index_tenant_id: str
    ownership_mode: str
    binding_source: str
    binding_verification_state: str
    verification_method: str
    vector_target_id: str
    vector_target_tenant_id: str
    embedding_profile_id: str
    embedding_profile_tenant_id: str
    vector_index_status: str
    vector_target_status: str
    embedding_profile_status: str
    external_verification_state: str | None
    external_verification_tenant_id: str | None
    sample_payload_keys: list[str]


def _normalized_payload_keys(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({str(item).strip() for item in value if str(item).strip()})


def validate_route_candidate(project_id: str, candidate: RouteCandidateContext) -> None:
    """Validate durable route eligibility without conflating it with live health.

    Target/Profile ``unavailable`` can be transient and is handled by activation/runtime
    preflight. ``disabled`` is an administrator-owned durable exclusion and is rejected.
    """

    if candidate.snapshot_project_id != project_id:
        raise ProjectVectorRouteStoreError(
            "SnapshotVectorBinding does not belong to the requested project"
        )
    if candidate.binding_verification_state != "verified":
        raise ProjectVectorRouteStoreError("SnapshotVectorBinding is not verified")
    if candidate.snapshot_status != "completed":
        raise ProjectVectorRouteStoreError("Snapshot is not completed")
    if candidate.vector_index_status != "ready":
        raise ProjectVectorRouteStoreError("VectorIndex is not ready")
    if candidate.vector_target_status == "disabled":
        raise ProjectVectorRouteStoreError("VectorTarget is disabled")
    if candidate.embedding_profile_status == "disabled":
        raise ProjectVectorRouteStoreError("EmbeddingProfile is disabled")

    tenant_values = {
        candidate.tenant_id,
        candidate.snapshot_tenant_id,
        candidate.vector_index_tenant_id,
        candidate.vector_target_tenant_id,
        candidate.embedding_profile_tenant_id,
    }
    if candidate.external_verification_tenant_id:
        tenant_values.add(candidate.external_verification_tenant_id)
    if len(tenant_values) != 1:
        raise ProjectVectorRouteStoreError(
            "Project vector route candidate crosses tenant boundaries"
        )

    if candidate.ownership_mode == "vision_managed":
        if candidate.binding_source != "managed_generation":
            raise ProjectVectorRouteStoreError(
                "Managed VectorIndex requires a managed_generation binding"
            )
        if not candidate.generation_id:
            raise ProjectVectorRouteStoreError(
                "Managed route candidate is missing IndexGeneration provenance"
            )
        if candidate.generation_status != "ready":
            raise ProjectVectorRouteStoreError(
                "Managed IndexGeneration is not ready"
            )
        return

    if candidate.ownership_mode == "external_attached":
        if candidate.binding_source != "external_verification":
            raise ProjectVectorRouteStoreError(
                "External VectorIndex requires an external_verification binding"
            )
        if candidate.generation_id is not None:
            raise ProjectVectorRouteStoreError(
                "External route candidate must not claim an IndexGeneration"
            )
        if candidate.external_verification_state != "compatible":
            raise ProjectVectorRouteStoreError(
                "External VectorIndex compatibility is not verified"
            )
        return

    raise ProjectVectorRouteStoreError(
        f"Unsupported VectorIndex ownership_mode: {candidate.ownership_mode}"
    )


def candidate_runtime_routable(candidate: RouteCandidateContext) -> bool:
    """Return whether the current pre-P3 Vision runtime can consume this payload.

    Managed P2 indexes are authored by Vision and always satisfy the inline Source payload
    contract. External indexes are only routable today when sampled payloads expose the
    fields consumed directly by ``ManagedVectorStoreFacade.search``. P3 hydration can later
    make locator-only external indexes routable without changing P2-H compatibility truth.
    """

    if candidate.ownership_mode == "vision_managed":
        return True
    if candidate.ownership_mode != "external_attached":
        return False
    required = {"content", "document_id", "chunk_id"}
    return required.issubset(set(candidate.sample_payload_keys))


class PostgresProjectVectorRouteStore:
    """P2-I project-scoped retrieval authority and append-only route audit ledger.

    ``project_vector_routes.active_binding_id`` is the sole retrieval pointer. Generation
    readiness remains build-state truth and is never used as a fallback routing authority.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._initialized = False
        self._initialize_lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._settings.postgres_password)

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(
            host=self._settings.postgres_host,
            port=self._settings.postgres_port,
            dbname=self._settings.postgres_db,
            user=self._settings.postgres_user,
            password=self._settings.postgres_password,
            connect_timeout=self._settings.postgres_connect_timeout_seconds,
            row_factory=dict_row,
        )

    def _ensure_schema(self) -> None:
        if not self.configured:
            raise ProjectVectorRouteStoreError(
                "PostgreSQL project vector route store is not configured"
            )
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            try:
                with self._connect() as connection:
                    require_schema(connection)
                self._initialized = True
            except (psycopg.Error, OSError, SchemaStateError) as exc:
                raise ProjectVectorRouteStoreError(
                    "PostgreSQL schema is not on the required Alembic revision"
                ) from exc

    @staticmethod
    def _route_record(row: dict[str, Any]) -> ProjectVectorRouteRecord:
        return ProjectVectorRouteRecord(
            project_id=str(row["project_id"]),
            tenant_id=str(row["tenant_id"]),
            active_binding_id=(
                str(row["active_binding_id"]) if row.get("active_binding_id") else None
            ),
            routing_mode=str(row["routing_mode"]),  # type: ignore[arg-type]
            revision=int(row["revision"]),
            selected_by=(str(row["selected_by"]) if row.get("selected_by") else None),
            selected_at=row.get("selected_at"),
            reason=(str(row["reason"]) if row.get("reason") else None),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _event_record(row: dict[str, Any]) -> ProjectVectorRouteEventRecord:
        return ProjectVectorRouteEventRecord(
            event_id=str(row["event_id"]),
            project_id=str(row["project_id"]),
            tenant_id=str(row["tenant_id"]),
            from_binding_id=(
                str(row["from_binding_id"]) if row.get("from_binding_id") else None
            ),
            to_binding_id=(
                str(row["to_binding_id"]) if row.get("to_binding_id") else None
            ),
            routing_mode=str(row["routing_mode"]),  # type: ignore[arg-type]
            actor=(str(row["actor"]) if row.get("actor") else None),
            reason=(str(row["reason"]) if row.get("reason") else None),
            revision=int(row["revision"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _candidate_record(row: dict[str, Any]) -> RouteCandidateContext:
        return RouteCandidateContext(
            binding_id=str(row["binding_id"]),
            tenant_id=str(row["binding_tenant_id"]),
            snapshot_id=str(row["snapshot_id"]),
            snapshot_project_id=str(row["snapshot_project_id"]),
            snapshot_status=str(row["snapshot_status"]),
            snapshot_tenant_id=str(row["snapshot_tenant_id"]),
            generation_id=(str(row["generation_id"]) if row.get("generation_id") else None),
            generation_status=(
                str(row["generation_status"]) if row.get("generation_status") else None
            ),
            vector_index_id=str(row["vector_index_id"]),
            vector_index_tenant_id=str(row["vector_index_tenant_id"]),
            ownership_mode=str(row["ownership_mode"]),
            binding_source=str(row["binding_source"]),
            binding_verification_state=str(row["binding_verification_state"]),
            verification_method=str(row["verification_method"]),
            vector_target_id=str(row["vector_target_id"]),
            vector_target_tenant_id=str(row["vector_target_tenant_id"]),
            embedding_profile_id=str(row["embedding_profile_id"]),
            embedding_profile_tenant_id=str(row["embedding_profile_tenant_id"]),
            vector_index_status=str(row["vector_index_status"]),
            vector_target_status=str(row["vector_target_status"]),
            embedding_profile_status=str(row["embedding_profile_status"]),
            external_verification_state=(
                str(row["external_verification_state"])
                if row.get("external_verification_state")
                else None
            ),
            external_verification_tenant_id=(
                str(row["external_verification_tenant_id"])
                if row.get("external_verification_tenant_id")
                else None
            ),
            sample_payload_keys=_normalized_payload_keys(row.get("sample_payload_keys")),
        )

    @staticmethod
    def _lock_project(connection: psycopg.Connection[dict[str, Any]], project_id: str) -> None:
        # Serialize route creation/update even when a row does not exist yet.
        connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (project_id,))

    @staticmethod
    def _select_route(
        connection: psycopg.Connection[dict[str, Any]],
        project_id: str,
        *,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if for_update else ""
        return connection.execute(
            """
            SELECT project_id, tenant_id, active_binding_id, routing_mode, revision,
                   selected_by, selected_at, reason, created_at, updated_at
            FROM project_vector_routes
            WHERE project_id = %s
            """ + suffix,
            (project_id,),
        ).fetchone()

    @classmethod
    def _candidate_context(
        cls,
        connection: psycopg.Connection[dict[str, Any]],
        binding_id: str,
        *,
        lock: bool = False,
    ) -> RouteCandidateContext | None:
        lock_clause = " FOR SHARE OF svb, ps, vi, vt, ep" if lock else ""
        row = connection.execute(
            """
            SELECT svb.binding_id,
                   svb.tenant_id AS binding_tenant_id,
                   svb.snapshot_id,
                   ps.project_id AS snapshot_project_id,
                   ps.status AS snapshot_status,
                   ps.tenant_id AS snapshot_tenant_id,
                   svb.generation_id,
                   ig.status AS generation_status,
                   svb.vector_index_id,
                   vi.tenant_id AS vector_index_tenant_id,
                   vi.ownership_mode,
                   svb.binding_source,
                   svb.verification_state AS binding_verification_state,
                   svb.verification_method,
                   vi.vector_target_id,
                   vt.tenant_id AS vector_target_tenant_id,
                   vi.embedding_profile_id,
                   ep.tenant_id AS embedding_profile_tenant_id,
                   vi.status AS vector_index_status,
                   vt.status AS vector_target_status,
                   ep.status AS embedding_profile_status,
                   ev.verification_state AS external_verification_state,
                   ev.tenant_id AS external_verification_tenant_id,
                   COALESCE(ev.sample_payload_keys, '[]'::jsonb) AS sample_payload_keys
            FROM snapshot_vector_bindings AS svb
            JOIN project_snapshots AS ps ON ps.snapshot_id = svb.snapshot_id
            JOIN vector_indexes AS vi ON vi.vector_index_id = svb.vector_index_id
            JOIN vector_targets AS vt ON vt.vector_target_id = vi.vector_target_id
            JOIN embedding_profiles AS ep ON ep.embedding_profile_id = vi.embedding_profile_id
            LEFT JOIN index_generations AS ig ON ig.generation_id = svb.generation_id
            LEFT JOIN external_vector_index_verifications AS ev
              ON ev.vector_index_id = svb.vector_index_id
             AND ev.tenant_id = svb.tenant_id
            WHERE svb.binding_id = %s
            """ + lock_clause,
            (binding_id,),
        ).fetchone()
        return cls._candidate_record(row) if row is not None else None

    @staticmethod
    def _insert_event(
        connection: psycopg.Connection[dict[str, Any]],
        *,
        project_id: str,
        tenant_id: str,
        from_binding_id: str | None,
        to_binding_id: str | None,
        routing_mode: RoutingMode,
        actor: str | None,
        reason: str | None,
        revision: int,
    ) -> None:
        connection.execute(
            """
            INSERT INTO project_vector_route_events (
                event_id, project_id, tenant_id, from_binding_id, to_binding_id,
                routing_mode, actor, reason, revision
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                f"pvrevt_{uuid4().hex}",
                project_id,
                tenant_id,
                from_binding_id,
                to_binding_id,
                routing_mode,
                actor,
                reason,
                revision,
            ),
        )

    def get(self, project_id: str) -> ProjectVectorRouteRecord | None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = self._select_route(connection, project_id)
                return self._route_record(row) if row is not None else None
        except (psycopg.Error, OSError) as exc:
            raise ProjectVectorRouteStoreError("Project vector route lookup failed") from exc

    def candidate_context(self, binding_id: str) -> RouteCandidateContext | None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                return self._candidate_context(connection, binding_id)
        except (psycopg.Error, OSError) as exc:
            raise ProjectVectorRouteStoreError("Project vector route candidate lookup failed") from exc

    def validate_candidate(self, project_id: str, candidate: RouteCandidateContext) -> None:
        validate_route_candidate(project_id, candidate)
        expected_tenant_id = (
            self._settings.snapshot_tenant_id.strip() or "vision-default"
        )
        if candidate.tenant_id != expected_tenant_id:
            raise ProjectVectorRouteStoreError(
                "Project vector route candidate is outside the configured tenant"
            )

    @staticmethod
    def current_runtime_routable(candidate: RouteCandidateContext) -> bool:
        return candidate_runtime_routable(candidate)

    def set_route(
        self,
        *,
        project_id: str,
        binding_id: str,
        routing_mode: RoutingMode,
        expected_revision: int,
        actor: str | None,
        reason: str | None,
    ) -> ProjectVectorRouteRecord:
        self._ensure_schema()
        if routing_mode not in {"managed_auto", "pinned"}:
            raise ProjectVectorRouteStoreError("Unsupported project vector routing mode")
        try:
            with self._connect() as connection:
                self._lock_project(connection, project_id)
                candidate = self._candidate_context(connection, binding_id, lock=True)
                if candidate is None:
                    raise ProjectVectorRouteStoreError("SnapshotVectorBinding not found")
                self.validate_candidate(project_id, candidate)
                if not candidate_runtime_routable(candidate):
                    raise ProjectVectorRouteStoreError(
                        "VECTOR_ROUTE_NOT_ROUTABLE: current Vision runtime cannot consume this payload contract"
                    )

                current = self._select_route(connection, project_id, for_update=True)
                current_revision = int(current["revision"]) if current else 0
                if current_revision != int(expected_revision):
                    raise ProjectVectorRouteConflict(
                        "VECTOR_ROUTE_CONFLICT: "
                        f"expected_revision={expected_revision}, current_revision={current_revision}"
                    )
                next_revision = current_revision + 1
                from_binding_id = (
                    str(current["active_binding_id"])
                    if current and current.get("active_binding_id")
                    else None
                )
                if current is None:
                    connection.execute(
                        """
                        INSERT INTO project_vector_routes (
                            project_id, tenant_id, active_binding_id, routing_mode, revision,
                            selected_by, selected_at, reason
                        ) VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s)
                        """,
                        (
                            project_id,
                            candidate.tenant_id,
                            binding_id,
                            routing_mode,
                            next_revision,
                            actor,
                            reason,
                        ),
                    )
                else:
                    if str(current["tenant_id"]) != candidate.tenant_id:
                        raise ProjectVectorRouteStoreError(
                            "Project vector route tenant cannot be changed"
                        )
                    connection.execute(
                        """
                        UPDATE project_vector_routes
                        SET active_binding_id = %s,
                            routing_mode = %s,
                            revision = %s,
                            selected_by = %s,
                            selected_at = NOW(),
                            reason = %s,
                            updated_at = NOW()
                        WHERE project_id = %s
                        """,
                        (
                            binding_id,
                            routing_mode,
                            next_revision,
                            actor,
                            reason,
                            project_id,
                        ),
                    )
                self._insert_event(
                    connection,
                    project_id=project_id,
                    tenant_id=candidate.tenant_id,
                    from_binding_id=from_binding_id,
                    to_binding_id=binding_id,
                    routing_mode=routing_mode,
                    actor=actor,
                    reason=reason,
                    revision=next_revision,
                )
                row = self._select_route(connection, project_id)
                if row is None:  # pragma: no cover - defensive transactional invariant
                    raise ProjectVectorRouteStoreError("Project vector route write disappeared")
                return self._route_record(row)
        except ProjectVectorRouteStoreError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise ProjectVectorRouteStoreError("Project vector route update failed") from exc

    def clear_route(
        self,
        *,
        project_id: str,
        expected_revision: int,
        actor: str | None,
        reason: str | None,
    ) -> ProjectVectorRouteRecord:
        """Explicitly clear retrieval and pin the empty route against auto promotion."""

        self._ensure_schema()
        try:
            with self._connect() as connection:
                self._lock_project(connection, project_id)
                current = self._select_route(connection, project_id, for_update=True)
                if current is None:
                    raise ProjectVectorRouteConflict(
                        "VECTOR_ROUTE_CONFLICT: project has no route to clear"
                    )
                current_revision = int(current["revision"])
                if current_revision != int(expected_revision):
                    raise ProjectVectorRouteConflict(
                        "VECTOR_ROUTE_CONFLICT: "
                        f"expected_revision={expected_revision}, current_revision={current_revision}"
                    )
                next_revision = current_revision + 1
                from_binding_id = (
                    str(current["active_binding_id"])
                    if current.get("active_binding_id")
                    else None
                )
                connection.execute(
                    """
                    UPDATE project_vector_routes
                    SET active_binding_id = NULL,
                        routing_mode = 'pinned',
                        revision = %s,
                        selected_by = %s,
                        selected_at = NOW(),
                        reason = %s,
                        updated_at = NOW()
                    WHERE project_id = %s
                    """,
                    (next_revision, actor, reason, project_id),
                )
                self._insert_event(
                    connection,
                    project_id=project_id,
                    tenant_id=str(current["tenant_id"]),
                    from_binding_id=from_binding_id,
                    to_binding_id=None,
                    routing_mode="pinned",
                    actor=actor,
                    reason=reason,
                    revision=next_revision,
                )
                row = self._select_route(connection, project_id)
                if row is None:  # pragma: no cover
                    raise ProjectVectorRouteStoreError("Project vector route clear disappeared")
                return self._route_record(row)
        except ProjectVectorRouteStoreError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise ProjectVectorRouteStoreError("Project vector route clear failed") from exc

    def promote_managed_binding(
        self,
        *,
        project_id: str,
        binding_id: str,
        actor: str | None,
        reason: str | None,
    ) -> ProjectVectorRouteRecord | None:
        """Auto-promote a verified managed build only while routing_mode=managed_auto.

        A pinned route (including an explicitly cleared route) is never displaced by a new
        managed build. This keeps build completion and retrieval selection independent.
        """

        self._ensure_schema()
        try:
            with self._connect() as connection:
                self._lock_project(connection, project_id)
                candidate = self._candidate_context(connection, binding_id, lock=True)
                if candidate is None:
                    raise ProjectVectorRouteStoreError("SnapshotVectorBinding not found")
                self.validate_candidate(project_id, candidate)
                if candidate.ownership_mode != "vision_managed":
                    raise ProjectVectorRouteStoreError(
                        "Automatic route promotion only accepts Vision-managed bindings"
                    )

                current = self._select_route(connection, project_id, for_update=True)
                if current is not None and str(current["routing_mode"]) != "managed_auto":
                    return self._route_record(current)
                if current is not None and current.get("active_binding_id") == binding_id:
                    return self._route_record(current)

                current_revision = int(current["revision"]) if current else 0
                next_revision = current_revision + 1
                from_binding_id = (
                    str(current["active_binding_id"])
                    if current and current.get("active_binding_id")
                    else None
                )
                if current is None:
                    connection.execute(
                        """
                        INSERT INTO project_vector_routes (
                            project_id, tenant_id, active_binding_id, routing_mode, revision,
                            selected_by, selected_at, reason
                        ) VALUES (%s, %s, %s, 'managed_auto', %s, %s, NOW(), %s)
                        """,
                        (
                            project_id,
                            candidate.tenant_id,
                            binding_id,
                            next_revision,
                            actor,
                            reason,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE project_vector_routes
                        SET active_binding_id = %s,
                            revision = %s,
                            selected_by = %s,
                            selected_at = NOW(),
                            reason = %s,
                            updated_at = NOW()
                        WHERE project_id = %s
                        """,
                        (binding_id, next_revision, actor, reason, project_id),
                    )
                self._insert_event(
                    connection,
                    project_id=project_id,
                    tenant_id=candidate.tenant_id,
                    from_binding_id=from_binding_id,
                    to_binding_id=binding_id,
                    routing_mode="managed_auto",
                    actor=actor,
                    reason=reason,
                    revision=next_revision,
                )
                row = self._select_route(connection, project_id)
                return self._route_record(row) if row is not None else None
        except ProjectVectorRouteStoreError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise ProjectVectorRouteStoreError(
                "Managed project vector route promotion failed"
            ) from exc

    def list_events(
        self, project_id: str, *, limit: int = 100
    ) -> list[ProjectVectorRouteEventRecord]:
        self._ensure_schema()
        resolved_limit = max(1, min(int(limit), 500))
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT event_id, project_id, tenant_id, from_binding_id, to_binding_id,
                           routing_mode, actor, reason, revision, created_at
                    FROM project_vector_route_events
                    WHERE project_id = %s
                    ORDER BY revision DESC, created_at DESC, event_id DESC
                    LIMIT %s
                    """,
                    (project_id, resolved_limit),
                ).fetchall()
                return [self._event_record(row) for row in rows]
        except (psycopg.Error, OSError) as exc:
            raise ProjectVectorRouteStoreError(
                "Project vector route event lookup failed"
            ) from exc
