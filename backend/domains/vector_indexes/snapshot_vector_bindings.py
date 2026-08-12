from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import Settings
from .schema_guard import SchemaStateError, require_schema


class SnapshotVectorBindingStoreError(RuntimeError):
    pass


def snapshot_vector_binding_id(tenant_id: str, snapshot_id: str, vector_index_id: str) -> str:
    material = "|".join((tenant_id.strip(), snapshot_id.strip(), vector_index_id.strip()))
    digest = hashlib.md5(material.encode("utf-8")).hexdigest()
    return f"svb_{digest[:24]}"


@dataclass(frozen=True)
class SnapshotVectorBindingRecord:
    binding_id: str
    tenant_id: str
    snapshot_id: str
    vector_index_id: str
    generation_id: str | None
    binding_source: str
    verification_state: str
    verification_method: str
    snapshot_fingerprint: str
    vector_index_identity_key: str
    verification_evidence: dict[str, Any]
    verified_at: datetime | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class PostgresSnapshotVectorBindingStore:
    """Persistent Snapshot <-> VectorIndex provenance registry.

    P2-G writes managed-generation bindings. P2-H may later add verified external
    bindings without requiring an IndexGeneration row.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._initialized = False
        self._initialize_lock = threading.Lock()

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
                raise SnapshotVectorBindingStoreError(
                    "PostgreSQL schema is not on the required Alembic revision"
                ) from exc

    @staticmethod
    def _columns() -> str:
        return (
            "binding_id, tenant_id, snapshot_id, vector_index_id, generation_id, "
            "binding_source, verification_state, verification_method, "
            "snapshot_fingerprint, vector_index_identity_key, verification_evidence, verified_at, error, "
            "created_at, updated_at"
        )

    @staticmethod
    def _record(row: dict[str, Any]) -> SnapshotVectorBindingRecord:
        return SnapshotVectorBindingRecord(
            binding_id=str(row["binding_id"]),
            tenant_id=str(row["tenant_id"]),
            snapshot_id=str(row["snapshot_id"]),
            vector_index_id=str(row["vector_index_id"]),
            generation_id=(str(row["generation_id"]) if row.get("generation_id") else None),
            binding_source=str(row["binding_source"]),
            verification_state=str(row["verification_state"]),
            verification_method=str(row["verification_method"]),
            snapshot_fingerprint=str(row["snapshot_fingerprint"]),
            vector_index_identity_key=str(row["vector_index_identity_key"]),
            verification_evidence=(
                dict(row.get("verification_evidence") or {})
                if isinstance(row.get("verification_evidence"), dict)
                else {}
            ),
            verified_at=row.get("verified_at"),
            error=(str(row["error"]) if row.get("error") else None),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def register_managed_generation(
        self,
        *,
        snapshot_id: str,
        generation_id: str,
        vector_index_id: str,
    ) -> SnapshotVectorBindingRecord:
        """Create/reopen a pending binding only when all managed provenance agrees."""
        self._ensure_schema()
        try:
            with self._connect() as connection:
                provenance = connection.execute(
                    """
                    SELECT ps.tenant_id, ps.fingerprint, ps.manifest_sha256,
                           ig.project_id, ig.snapshot_id, ig.vector_index_id,
                           vi.identity_key, vi.ownership_mode, vi.selector
                    FROM index_generations AS ig
                    JOIN project_snapshots AS ps ON ps.snapshot_id = ig.snapshot_id
                    JOIN vector_indexes AS vi ON vi.vector_index_id = ig.vector_index_id
                    WHERE ig.generation_id = %s
                      AND ig.snapshot_id = %s
                      AND ig.vector_index_id = %s
                    """,
                    (generation_id, snapshot_id, vector_index_id),
                ).fetchone()
                if provenance is None:
                    raise SnapshotVectorBindingStoreError(
                        "Managed Snapshot/Generation/VectorIndex provenance does not agree"
                    )
                selector = provenance.get("selector") or {}
                if provenance.get("ownership_mode") != "vision_managed" or not isinstance(selector, dict):
                    raise SnapshotVectorBindingStoreError(
                        "Managed binding requires a Vision-managed VectorIndex"
                    )
                if selector.get("project_id") != provenance.get("project_id") or selector.get("generation_id") != generation_id:
                    raise SnapshotVectorBindingStoreError(
                        "Managed VectorIndex selector does not match its generation"
                    )
                tenant_id = str(
                    provenance.get("tenant_id") or self._settings.snapshot_tenant_id
                )
                fingerprint = str(provenance.get("fingerprint") or provenance.get("manifest_sha256") or "").strip()
                identity_key = str(provenance.get("identity_key") or "").strip()
                if not fingerprint or not identity_key:
                    raise SnapshotVectorBindingStoreError(
                        "Snapshot/VectorIndex immutable identity is incomplete"
                    )
                binding_id = snapshot_vector_binding_id(tenant_id, snapshot_id, vector_index_id)
                row = connection.execute(
                    f"""
                    INSERT INTO snapshot_vector_bindings (
                        binding_id, tenant_id, snapshot_id, vector_index_id, generation_id,
                        binding_source, verification_state, verification_method,
                        snapshot_fingerprint, vector_index_identity_key, verification_evidence,
                        verified_at, error, created_at, updated_at
                    ) VALUES (
                        %s,%s,%s,%s,%s,'managed_generation','pending','managed_build',
                        %s,%s,'{{}}'::jsonb,NULL,NULL,NOW(),NOW()
                    )
                    ON CONFLICT (snapshot_id, vector_index_id) DO UPDATE SET
                        generation_id = EXCLUDED.generation_id,
                        binding_source = 'managed_generation',
                        verification_state = CASE
                            WHEN snapshot_vector_bindings.verification_state = 'verified'
                            THEN 'verified' ELSE 'pending' END,
                        verification_method = 'managed_build',
                        snapshot_fingerprint = EXCLUDED.snapshot_fingerprint,
                        vector_index_identity_key = EXCLUDED.vector_index_identity_key,
                        error = NULL,
                        updated_at = NOW()
                    WHERE snapshot_vector_bindings.binding_source = 'managed_generation'
                      AND snapshot_vector_bindings.generation_id = EXCLUDED.generation_id
                    RETURNING {self._columns()}
                    """,
                    (binding_id, tenant_id, snapshot_id, vector_index_id, generation_id, fingerprint, identity_key),
                ).fetchone()
            if row is None:
                raise SnapshotVectorBindingStoreError("SnapshotVectorBinding upsert returned no row")
            return self._record(row)
        except SnapshotVectorBindingStoreError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise SnapshotVectorBindingStoreError("SnapshotVectorBinding write failed") from exc

    def register_external_verification(
        self,
        *,
        snapshot_id: str,
        vector_index_id: str,
        verification_method: str,
        verification_evidence: dict[str, Any],
    ) -> SnapshotVectorBindingRecord:
        """Create a generation-less verified binding for a compatible external index."""
        if verification_method not in {"external_probe", "manual"}:
            raise SnapshotVectorBindingStoreError(
                f"Unsupported external binding verification method: {verification_method}"
            )
        self._ensure_schema()
        try:
            with self._connect() as connection:
                provenance = connection.execute(
                    """
                    SELECT ps.tenant_id, ps.fingerprint, ps.manifest_sha256,
                           vi.tenant_id AS vector_tenant_id,
                           vi.identity_key, vi.ownership_mode, vi.status,
                           ev.verification_state AS external_verification_state
                    FROM project_snapshots AS ps
                    JOIN vector_indexes AS vi ON vi.vector_index_id = %s
                    LEFT JOIN external_vector_index_verifications AS ev
                      ON ev.vector_index_id = vi.vector_index_id
                    WHERE ps.snapshot_id = %s
                    """,
                    (vector_index_id, snapshot_id),
                ).fetchone()
                if provenance is None:
                    raise SnapshotVectorBindingStoreError(
                        "External Snapshot/VectorIndex provenance cannot be resolved"
                    )
                if provenance.get("ownership_mode") != "external_attached":
                    raise SnapshotVectorBindingStoreError(
                        "External binding requires an external_attached VectorIndex"
                    )
                snapshot_tenant_id = str(
                    provenance.get("tenant_id") or self._settings.snapshot_tenant_id
                )
                vector_tenant_id = str(provenance.get("vector_tenant_id") or "")
                if not vector_tenant_id or vector_tenant_id != snapshot_tenant_id:
                    raise SnapshotVectorBindingStoreError(
                        "Snapshot and external VectorIndex tenant boundaries do not match"
                    )
                if provenance.get("status") != "ready" or provenance.get("external_verification_state") != "compatible":
                    raise SnapshotVectorBindingStoreError(
                        "External VectorIndex must be structurally compatible and ready before Snapshot binding"
                    )
                tenant_id = snapshot_tenant_id
                fingerprint = str(
                    provenance.get("fingerprint") or provenance.get("manifest_sha256") or ""
                ).strip()
                identity_key = str(provenance.get("identity_key") or "").strip()
                if not fingerprint or not identity_key:
                    raise SnapshotVectorBindingStoreError(
                        "Snapshot/VectorIndex immutable identity is incomplete"
                    )
                binding_id = snapshot_vector_binding_id(tenant_id, snapshot_id, vector_index_id)
                row = connection.execute(
                    f"""
                    INSERT INTO snapshot_vector_bindings (
                        binding_id, tenant_id, snapshot_id, vector_index_id, generation_id,
                        binding_source, verification_state, verification_method,
                        snapshot_fingerprint, vector_index_identity_key, verification_evidence,
                        verified_at, error, created_at, updated_at
                    ) VALUES (
                        %s,%s,%s,%s,NULL,'external_verification','verified',%s,
                        %s,%s,%s,NOW(),NULL,NOW(),NOW()
                    )
                    ON CONFLICT (snapshot_id, vector_index_id) DO UPDATE SET
                        binding_source='external_verification',
                        verification_state='verified',
                        verification_method=EXCLUDED.verification_method,
                        snapshot_fingerprint=EXCLUDED.snapshot_fingerprint,
                        vector_index_identity_key=EXCLUDED.vector_index_identity_key,
                        verification_evidence=EXCLUDED.verification_evidence,
                        verified_at=NOW(),
                        error=NULL,
                        updated_at=NOW()
                    WHERE snapshot_vector_bindings.binding_source='external_verification'
                      AND snapshot_vector_bindings.generation_id IS NULL
                    RETURNING {self._columns()}
                    """,
                    (
                        binding_id, tenant_id, snapshot_id, vector_index_id, verification_method,
                        fingerprint, identity_key, Jsonb(verification_evidence),
                    ),
                ).fetchone()
            if row is None:
                raise SnapshotVectorBindingStoreError(
                    "External SnapshotVectorBinding conflicts with an existing managed binding"
                )
            return self._record(row)
        except SnapshotVectorBindingStoreError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise SnapshotVectorBindingStoreError(
                "External SnapshotVectorBinding write failed"
            ) from exc

    def get(self, binding_id: str) -> SnapshotVectorBindingRecord | None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    f"SELECT {self._columns()} FROM snapshot_vector_bindings WHERE binding_id=%s",
                    (binding_id,),
                ).fetchone()
            return self._record(row) if row else None
        except (psycopg.Error, OSError) as exc:
            raise SnapshotVectorBindingStoreError("SnapshotVectorBinding lookup failed") from exc

    def get_verified_managed(
        self,
        *,
        snapshot_id: str,
        generation_id: str,
        vector_index_id: str,
    ) -> SnapshotVectorBindingRecord | None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    f"""
                    SELECT {self._columns()}
                    FROM snapshot_vector_bindings
                    WHERE snapshot_id=%s
                      AND generation_id=%s
                      AND vector_index_id=%s
                      AND binding_source='managed_generation'
                      AND verification_state='verified'
                    """,
                    (snapshot_id, generation_id, vector_index_id),
                ).fetchone()
            return self._record(row) if row else None
        except (psycopg.Error, OSError) as exc:
            raise SnapshotVectorBindingStoreError("Verified binding lookup failed") from exc

    def list(self, *, tenant_id: str | None = None) -> list[SnapshotVectorBindingRecord]:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                if tenant_id:
                    rows = connection.execute(
                        f"SELECT {self._columns()} FROM snapshot_vector_bindings WHERE tenant_id=%s ORDER BY created_at, binding_id",
                        (tenant_id,),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        f"SELECT {self._columns()} FROM snapshot_vector_bindings ORDER BY created_at, binding_id"
                    ).fetchall()
            return [self._record(row) for row in rows]
        except (psycopg.Error, OSError) as exc:
            raise SnapshotVectorBindingStoreError("SnapshotVectorBinding list failed") from exc

