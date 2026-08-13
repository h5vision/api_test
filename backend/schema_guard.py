from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


BASELINE_REVISION = "p2a_0001_baseline"
CURRENT_REVISION = "p3_0010_external_project_registry"

# P2-A adopts the complete PostgreSQL schema currently owned by Vision.  The
# list is intentionally broader than Snapshot/Vector state: once Alembic owns
# the database, no Postgres-backed store may silently create its own tables.
BASELINE_TABLE_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "projects": (
        "project_id", "display_name", "current_snapshot_id", "manifest_sha256",
        "git_commit_sha", "git_branch", "git_dirty", "git_committed_at",
        "source_modified_at", "index_completed_at", "embedding_model",
        "embedding_model_id", "index_version", "index_status",
        "active_generation_id", "created_at", "updated_at",
    ),
    "repository_sources": (
        "source_id", "project_id", "source_type", "root_relative_path",
        "repository_url", "default_branch", "enabled", "last_revision",
        "last_synced_at", "tenant_id", "provider_repository_id",
        "created_at", "updated_at",
    ),
    "project_snapshots": (
        "snapshot_id", "project_id", "source_id", "revision", "git_branch",
        "git_dirty", "git_committed_at", "manifest_sha256", "file_count",
        "total_bytes", "status", "tenant_id", "snapshot_kind", "tree_sha",
        "fingerprint", "verified_by", "verified_at", "locator", "created_at",
        "completed_at",
    ),
    "snapshot_entries": (
        "snapshot_id", "relative_path", "name", "entry_type", "language",
        "size_bytes", "content_sha256", "content", "indexable", "metadata",
    ),
    "index_generations": (
        "generation_id", "project_id", "snapshot_id", "collection_name",
        "embedding_model", "embedding_model_id", "index_version", "status",
        "vector_index_id", "file_count", "chunk_count", "error", "created_at",
        "activated_at", "ready_at",
    ),
    "generation_chunks": (
        "generation_id", "chunk_id", "document_id", "relative_path",
        "external_point_id", "content_sha256", "content", "line_start",
        "line_end", "metadata",
    ),
    "repository_index_jobs": (
        "job_id", "source_id", "project_id", "snapshot_id", "generation_id",
        "status", "stage", "force_run", "files_total", "files_processed",
        "chunks_stored", "bytes_total", "error", "created_at", "updated_at",
        "completed_at",
    ),
    "document_versions": (
        "document_version_id", "project_id", "document_id", "path", "language",
        "content_sha256", "content", "metadata", "is_current", "created_at",
    ),
    "document_chunks": (
        "chunk_id", "document_version_id", "project_id", "document_id",
        "content", "content_sha256", "line_start", "line_end", "metadata",
        "created_at",
    ),
    "vector_mappings": (
        "chunk_id", "external_point_id", "collection_name", "embedding_model",
        "index_version", "updated_at",
    ),
    "runtime_service_settings": (
        "singleton", "groq_enabled", "groq_base_url", "groq_model",
        "default_model_id", "vector_host", "vector_port", "vector_target_id", "vector_collection",
        "embedding_profile_id", "embedding_model", "index_version", "embedding_deployment",
        "embedding_provider", "embedding_base_url", "embedding_model_id",
        "embedding_dimension", "embedding_batch_size", "updated_at",
    ),
    "vector_targets": (
        "vector_target_id", "tenant_id", "name", "engine", "endpoint",
        "credential_ref", "deployment_type", "capabilities", "status",
        "error", "latency_ms", "last_checked_at", "created_at", "updated_at",
    ),
    "embedding_profiles": (
        "embedding_profile_id", "tenant_id", "name", "deployment", "provider",
        "base_url", "model", "model_id", "dimension", "batch_size",
        "credential_ref", "status", "error", "latency_ms", "last_checked_at",
        "created_at", "updated_at",
    ),
    "vector_indexes": (
        "vector_index_id", "tenant_id", "name", "vector_target_id",
        "embedding_profile_id", "collection", "selector", "index_version",
        "distance_metric", "ownership_mode", "query_strategy", "status",
        "identity_key", "created_at", "updated_at",
    ),
    "snapshot_vector_bindings": (
        "binding_id", "tenant_id", "snapshot_id", "vector_index_id",
        "generation_id", "binding_source", "verification_state",
        "verification_method", "snapshot_fingerprint",
        "vector_index_identity_key", "verification_evidence", "verified_at", "error",
        "created_at", "updated_at",
    ),
    "external_vector_index_verifications": (
        "vector_index_id", "tenant_id", "verification_state", "verification_method",
        "embedding_profile_attested", "expected_dimension", "observed_dimension",
        "expected_distance_metric", "observed_distance_metric", "observed_vector_type",
        "observed_points_count", "selector_points_count", "sample_size",
        "sample_payload_keys", "last_verified_at", "error", "created_at", "updated_at",
    ),
    "project_vector_routes": (
        "project_id", "tenant_id", "active_binding_id", "routing_mode",
        "revision", "selected_by", "selected_at", "reason", "created_at", "updated_at",
    ),
    "project_vector_route_events": (
        "event_id", "project_id", "tenant_id", "from_binding_id", "to_binding_id",
        "routing_mode", "actor", "reason", "revision", "created_at",
    ),
    "runtime_network_settings": (
        "singleton", "frontend_ip", "frontend_port", "backendai_ip",
        "backendai_port", "updated_at",
    ),
    "ai_provider_configs": (
        "provider_id", "name", "protocol", "base_url", "auth_type",
        "api_key_ciphertext", "api_key_hint", "enabled", "deployment_type",
        "chat_processing_mode", "status", "error", "latency_ms", "model_count", "last_checked_at",
        "created_at", "updated_at",
    ),
    "ai_provider_models": (
        "provider_id", "model_name", "discovered_at",
    ),
    "model_access_policies": ("model_id", "enabled", "updated_at"),
    "client_connections": (
        "client_id", "client_type", "project_id", "client_version", "last_event",
        "details", "first_seen_at", "last_seen_at",
    ),
    "frontend_api_activity": (
        "client_id", "method", "path", "request_count", "success_count",
        "error_count", "last_status_code", "last_request_at", "last_response_at",
        "last_success_at", "last_duration_ms", "last_request_id",
    ),
    "communication_events": (
        "event_id", "occurred_at", "request_id", "channel", "direction", "phase",
        "status", "method", "path", "client_id", "project_id", "status_code",
        "duration_ms", "provider", "model", "source_count", "error", "details",
    ),
    "chat_audit_logs": (
        "request_id", "received_at", "completed_at", "client_id", "project_id",
        "session_id", "requested_model_id", "message", "message_truncated",
        "history_count", "context_chars", "status", "status_code", "answer",
        "answer_truncated", "used_model_id", "provider", "source_count",
        "duration_ms", "error",
    ),
    "frontend_registration_events": (
        "event_id", "occurred_at", "request_id", "event_type", "status",
        "client_id", "instance_id", "client_name", "declared_user",
        "client_version", "source_ip", "registration_type",
        "identification_method", "is_first_connection", "reason",
    ),
    "frontend_clients": (
        "client_id", "name", "ip", "port", "enabled", "instance_id",
        "registration_type", "last_seen_ip", "last_seen_at", "created_at",
        "updated_at", "chat_deep_normalization_mode",
    ),
    "chat_intake_settings": (
        "singleton", "deep_normalization_enabled", "fallback_mode", "updated_at",
    ),
    "rag_targets": (
        "target_id", "name", "base_url", "enabled", "availability",
        "last_seen_at", "error", "created_at", "updated_at",
    ),
    "external_project_catalog": (
        "target_id", "external_project_id", "name", "state", "revision", "dirty",
        "chunk_count", "actual_chunks", "indexed_at", "fingerprint", "availability",
        "last_seen_at", "raw_metadata", "created_at", "updated_at",
    ),
    "project_external_bindings": (
        "project_id", "target_id", "external_project_id", "binding_method",
        "binding_strength", "verification_state", "last_verified_at", "error",
        "created_at", "updated_at",
    ),
    "frontend_metadata": (
        "metadata_id", "project_id", "session_id", "scope", "entity_id",
        "source", "payload", "created_at", "updated_at",
    ),
    "frontend_documents": (
        "project_id", "document_id", "path", "language", "type", "string_value",
        "details", "created_at", "updated_at",
    ),
}


CURRENT_TABLE_COLUMNS = BASELINE_TABLE_COLUMNS
P2A_BASELINE_TABLE_COLUMNS: Mapping[str, tuple[str, ...]] = {
    table: tuple(columns) for table, columns in CURRENT_TABLE_COLUMNS.items()
}
P2A_BASELINE_TABLE_COLUMNS = dict(P2A_BASELINE_TABLE_COLUMNS)
P2A_BASELINE_TABLE_COLUMNS.pop("vector_targets", None)
P2A_BASELINE_TABLE_COLUMNS.pop("embedding_profiles", None)
P2A_BASELINE_TABLE_COLUMNS.pop("vector_indexes", None)
P2A_BASELINE_TABLE_COLUMNS.pop("snapshot_vector_bindings", None)
P2A_BASELINE_TABLE_COLUMNS.pop("external_vector_index_verifications", None)
P2A_BASELINE_TABLE_COLUMNS.pop("project_vector_routes", None)
P2A_BASELINE_TABLE_COLUMNS.pop("project_vector_route_events", None)
P2A_BASELINE_TABLE_COLUMNS.pop("chat_intake_settings", None)
P2A_BASELINE_TABLE_COLUMNS.pop("rag_targets", None)
P2A_BASELINE_TABLE_COLUMNS.pop("external_project_catalog", None)
P2A_BASELINE_TABLE_COLUMNS.pop("project_external_bindings", None)
P2A_BASELINE_TABLE_COLUMNS["frontend_clients"] = tuple(
    column
    for column in P2A_BASELINE_TABLE_COLUMNS["frontend_clients"]
    if column != "chat_deep_normalization_mode"
)
P2A_BASELINE_TABLE_COLUMNS["runtime_service_settings"] = tuple(
    column
    for column in P2A_BASELINE_TABLE_COLUMNS["runtime_service_settings"]
    if column not in {"vector_target_id", "embedding_profile_id"}
)
P2A_BASELINE_TABLE_COLUMNS["index_generations"] = tuple(
    column
    for column in P2A_BASELINE_TABLE_COLUMNS["index_generations"]
    if column not in {"vector_index_id", "ready_at"}
)
P2A_BASELINE_TABLE_COLUMNS["ai_provider_configs"] = tuple(
    column
    for column in P2A_BASELINE_TABLE_COLUMNS["ai_provider_configs"]
    if column != "chat_processing_mode"
)


class SchemaStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class SchemaInspection:
    revision: str | None
    missing_tables: tuple[str, ...]
    missing_columns: tuple[str, ...]

    @property
    def baseline_compatible(self) -> bool:
        return not self.missing_tables and not self.missing_columns


def _inspect_schema(
    connection: Any,
    required_table_columns: Mapping[str, tuple[str, ...]],
) -> SchemaInspection:
    version_table = connection.execute(
        "SELECT to_regclass('public.alembic_version') AS name"
    ).fetchone()
    revision: str | None = None
    if version_table and version_table.get("name"):
        row = connection.execute(
            "SELECT version_num FROM alembic_version LIMIT 1"
        ).fetchone()
        if row and row.get("version_num"):
            revision = str(row["version_num"])

    rows = connection.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
        """
    ).fetchall()
    observed: dict[str, set[str]] = {}
    for row in rows:
        observed.setdefault(str(row["table_name"]), set()).add(str(row["column_name"]))

    missing_tables: list[str] = []
    missing_columns: list[str] = []
    for table, required_columns in required_table_columns.items():
        columns = observed.get(table)
        if columns is None:
            missing_tables.append(table)
            continue
        for column in required_columns:
            if column not in columns:
                missing_columns.append(f"{table}.{column}")

    return SchemaInspection(
        revision=revision,
        missing_tables=tuple(sorted(missing_tables)),
        missing_columns=tuple(sorted(missing_columns)),
    )


def inspect_schema(connection: Any) -> SchemaInspection:
    """Inspect the current runtime-required Vision schema contract."""
    return _inspect_schema(connection, CURRENT_TABLE_COLUMNS)


def inspect_p2a_baseline_schema(connection: Any) -> SchemaInspection:
    """Inspect only the historical P2-A adoption shape."""
    return _inspect_schema(connection, P2A_BASELINE_TABLE_COLUMNS)


def require_schema(connection: Any) -> None:
    inspection = inspect_schema(connection)
    if not inspection.baseline_compatible:
        details = []
        if inspection.missing_tables:
            details.append("missing tables=" + ",".join(inspection.missing_tables))
        if inspection.missing_columns:
            details.append("missing columns=" + ",".join(inspection.missing_columns))
        raise SchemaStateError(
            "PostgreSQL schema does not match the current Vision schema contract ("
            + "; ".join(details)
            + "). Run `alembic upgrade head` after any required baseline adoption."
        )
    if inspection.revision != CURRENT_REVISION:
        if inspection.revision is None:
            raise SchemaStateError(
                "PostgreSQL schema is structurally compatible but is not Alembic-managed. "
                "Adopt the P2-A baseline first when needed, then run `alembic upgrade head`."
            )
        raise SchemaStateError(
            "Unsupported PostgreSQL schema revision: "
            f"expected={CURRENT_REVISION}, actual={inspection.revision}. "
            "Run `alembic upgrade head`."
        )
