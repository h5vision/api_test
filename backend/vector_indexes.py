from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import Settings
from .schema_guard import SchemaStateError, require_schema


class VectorIndexStoreError(RuntimeError):
    pass


def canonical_vector_selector(selector: Mapping[str, Any] | None) -> dict[str, Any]:
    return json.loads(json.dumps(dict(selector or {}), ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def vector_index_identity(
    tenant_id: str,
    vector_target_id: str,
    embedding_profile_id: str,
    collection: str,
    selector: Mapping[str, Any] | None,
    index_version: str,
    distance_metric: str,
) -> tuple[str, str]:
    canonical_selector = canonical_vector_selector(selector)
    material = "|".join(
        (
            tenant_id.strip(),
            vector_target_id.strip(),
            embedding_profile_id.strip(),
            collection.strip(),
            json.dumps(canonical_selector, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            index_version.strip(),
            distance_metric.strip().lower(),
        )
    )
    digest = hashlib.md5(material.encode("utf-8")).hexdigest()
    return f"vidx_{digest[:24]}", digest


@dataclass(frozen=True)
class VectorIndexRecord:
    vector_index_id: str
    tenant_id: str
    name: str
    vector_target_id: str
    embedding_profile_id: str
    collection: str
    selector: dict[str, Any]
    index_version: str
    distance_metric: str
    ownership_mode: str
    query_strategy: str
    status: str
    identity_key: str
    created_at: datetime
    updated_at: datetime


class PostgresVectorIndexStore:
    """Persistent registry for concrete logical vector datasets.

    VectorIndex is not a physical collection and never contains placeholder selector
    variables. Managed generations use a concrete {project_id, generation_id} selector.
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
                raise VectorIndexStoreError(
                    "PostgreSQL schema is not on the required Alembic revision"
                ) from exc

    @staticmethod
    def _record(row: dict[str, Any]) -> VectorIndexRecord:
        selector = row.get("selector") or {}
        return VectorIndexRecord(
            vector_index_id=str(row["vector_index_id"]),
            tenant_id=str(row["tenant_id"]),
            name=str(row["name"]),
            vector_target_id=str(row["vector_target_id"]),
            embedding_profile_id=str(row["embedding_profile_id"]),
            collection=str(row["collection"]),
            selector=dict(selector) if isinstance(selector, dict) else {},
            index_version=str(row["index_version"]),
            distance_metric=str(row["distance_metric"]),
            ownership_mode=str(row["ownership_mode"]),
            query_strategy=str(row["query_strategy"]),
            status=str(row["status"]),
            identity_key=str(row["identity_key"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _columns() -> str:
        return (
            "vector_index_id, tenant_id, name, vector_target_id, embedding_profile_id, "
            "collection, selector, index_version, distance_metric, ownership_mode, "
            "query_strategy, status, identity_key, created_at, updated_at"
        )

    def list(self, *, tenant_id: str | None = None) -> list[VectorIndexRecord]:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                if tenant_id:
                    rows = connection.execute(
                        f"SELECT {self._columns()} FROM vector_indexes WHERE tenant_id=%s ORDER BY updated_at DESC, vector_index_id",
                        (tenant_id,),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        f"SELECT {self._columns()} FROM vector_indexes ORDER BY updated_at DESC, vector_index_id"
                    ).fetchall()
            return [self._record(row) for row in rows]
        except (psycopg.Error, OSError) as exc:
            raise VectorIndexStoreError("VectorIndex list failed") from exc

    def get(self, vector_index_id: str) -> VectorIndexRecord | None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    f"SELECT {self._columns()} FROM vector_indexes WHERE vector_index_id=%s",
                    (vector_index_id,),
                ).fetchone()
            return self._record(row) if row else None
        except (psycopg.Error, OSError) as exc:
            raise VectorIndexStoreError("VectorIndex lookup failed") from exc

    def upsert(
        self,
        *,
        tenant_id: str,
        name: str,
        vector_target_id: str,
        embedding_profile_id: str,
        collection: str,
        selector: Mapping[str, Any] | None,
        index_version: str,
        distance_metric: str = "cosine",
        ownership_mode: str = "vision_managed",
        query_strategy: str = "qdrant-query-api",
        status: str = "building",
    ) -> VectorIndexRecord:
        self._ensure_schema()
        canonical_selector = canonical_vector_selector(selector)
        vector_index_id, identity_key = vector_index_identity(
            tenant_id,
            vector_target_id,
            embedding_profile_id,
            collection,
            canonical_selector,
            index_version,
            distance_metric,
        )
        try:
            with self._connect() as connection:
                row = connection.execute(
                    f"""
                    INSERT INTO vector_indexes (
                        vector_index_id, tenant_id, name, vector_target_id,
                        embedding_profile_id, collection, selector, index_version,
                        distance_metric, ownership_mode, query_strategy, status,
                        identity_key, created_at, updated_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
                    ON CONFLICT (identity_key) DO UPDATE SET
                        name=EXCLUDED.name,
                        ownership_mode=EXCLUDED.ownership_mode,
                        query_strategy=EXCLUDED.query_strategy,
                        status=EXCLUDED.status,
                        updated_at=NOW()
                    RETURNING {self._columns()}
                    """,
                    (
                        vector_index_id, tenant_id, name, vector_target_id,
                        embedding_profile_id, collection, Jsonb(canonical_selector),
                        index_version, distance_metric.lower(), ownership_mode,
                        query_strategy, status, identity_key,
                    ),
                ).fetchone()
            if row is None:
                raise VectorIndexStoreError("VectorIndex upsert returned no row")
            return self._record(row)
        except (psycopg.Error, OSError) as exc:
            raise VectorIndexStoreError("VectorIndex upsert failed") from exc

    def register_external(
        self,
        *,
        tenant_id: str,
        name: str,
        vector_target_id: str,
        embedding_profile_id: str,
        collection: str,
        selector: Mapping[str, Any] | None,
        index_version: str,
        distance_metric: str,
        query_strategy: str = "qdrant-query-api",
    ) -> VectorIndexRecord:
        """Attach an existing logical dataset without mutating its vector space.

        The same physical logical boundary cannot be re-declared with a different
        EmbeddingProfile. Such a change is a different vector space and requires an
        independently managed dataset rather than silent metadata mutation.
        """
        self._ensure_schema()
        canonical_selector = canonical_vector_selector(selector)
        vector_index_id, identity_key = vector_index_identity(
            tenant_id,
            vector_target_id,
            embedding_profile_id,
            collection,
            canonical_selector,
            index_version,
            distance_metric,
        )
        try:
            with self._connect() as connection:
                conflict = connection.execute(
                    f"""
                    SELECT {self._columns()}
                    FROM vector_indexes
                    WHERE tenant_id=%s
                      AND vector_target_id=%s
                      AND collection=%s
                      AND selector=%s::jsonb
                      AND index_version=%s
                      AND status <> 'disabled'
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (
                        tenant_id,
                        vector_target_id,
                        collection,
                        json.dumps(canonical_selector, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                        index_version,
                    ),
                ).fetchone()
                if conflict is not None:
                    existing = self._record(conflict)
                    if existing.vector_index_id == vector_index_id:
                        if existing.ownership_mode != "external_attached":
                            raise VectorIndexStoreError(
                                "Logical dataset is already Vision-managed and cannot be reattached as external"
                            )
                        return existing
                    if existing.embedding_profile_id != embedding_profile_id:
                        raise VectorIndexStoreError(
                            "External logical dataset is already attached with a different EmbeddingProfile; "
                            "stored vectors are not silently reinterpreted in another vector space"
                        )
                    raise VectorIndexStoreError(
                        "External logical dataset conflicts with an existing VectorIndex descriptor"
                    )

                row = connection.execute(
                    f"""
                    INSERT INTO vector_indexes (
                        vector_index_id, tenant_id, name, vector_target_id,
                        embedding_profile_id, collection, selector, index_version,
                        distance_metric, ownership_mode, query_strategy, status,
                        identity_key, created_at, updated_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'external_attached',%s,'unavailable',%s,NOW(),NOW())
                    RETURNING {self._columns()}
                    """,
                    (
                        vector_index_id, tenant_id, name, vector_target_id,
                        embedding_profile_id, collection, Jsonb(canonical_selector),
                        index_version, distance_metric.lower(), query_strategy, identity_key,
                    ),
                ).fetchone()
            if row is None:
                raise VectorIndexStoreError("External VectorIndex attach returned no row")
            return self._record(row)
        except VectorIndexStoreError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise VectorIndexStoreError("External VectorIndex attach failed") from exc

    def register_generation(
        self,
        *,
        tenant_id: str,
        project_id: str,
        generation_id: str,
        vector_target_id: str,
        embedding_profile_id: str,
        collection: str,
        index_version: str,
        status: str = "building",
    ) -> VectorIndexRecord:
        existing = self.get_generation(project_id, generation_id)
        if existing is not None:
            if (
                existing.vector_target_id != vector_target_id
                or existing.embedding_profile_id != embedding_profile_id
                or existing.collection != collection
                or existing.index_version != index_version
            ):
                raise VectorIndexStoreError(
                    "Generation is already bound to an incompatible VectorIndex descriptor"
                )
            updated = self.update_status(existing.vector_index_id, status)
            return updated or existing
        return self.upsert(
            tenant_id=tenant_id,
            name=f"{project_id} · {generation_id}",
            vector_target_id=vector_target_id,
            embedding_profile_id=embedding_profile_id,
            collection=collection,
            selector={"project_id": project_id, "generation_id": generation_id},
            index_version=index_version,
            distance_metric="cosine",
            ownership_mode="vision_managed",
            query_strategy="qdrant-query-api",
            status=status,
        )

    def get_generation(self, project_id: str, generation_id: str) -> VectorIndexRecord | None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    f"""
                    SELECT {self._columns()}
                    FROM vector_indexes
                    WHERE ownership_mode='vision_managed'
                      AND selector @> %s::jsonb
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (json.dumps({"project_id": project_id, "generation_id": generation_id}),),
                ).fetchone()
            return self._record(row) if row else None
        except (psycopg.Error, OSError) as exc:
            raise VectorIndexStoreError("Generation VectorIndex lookup failed") from exc

    def update_status(self, vector_index_id: str, status: str) -> VectorIndexRecord | None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    f"UPDATE vector_indexes SET status=%s, updated_at=NOW() WHERE vector_index_id=%s RETURNING {self._columns()}",
                    (status, vector_index_id),
                ).fetchone()
            return self._record(row) if row else None
        except (psycopg.Error, OSError) as exc:
            raise VectorIndexStoreError("VectorIndex status update failed") from exc

