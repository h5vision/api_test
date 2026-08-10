from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import Settings
from .schema_guard import SchemaStateError, require_schema
from .vector_store import VectorIndexState, VectorPointSample


class ExternalVectorIndexVerificationStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExternalVectorIndexVerificationRecord:
    vector_index_id: str
    tenant_id: str
    verification_state: str
    verification_method: str
    embedding_profile_attested: bool
    expected_dimension: int
    observed_dimension: int | None
    expected_distance_metric: str
    observed_distance_metric: str | None
    observed_vector_type: str | None
    observed_points_count: int | None
    selector_points_count: int | None
    sample_size: int
    sample_payload_keys: list[str]
    last_verified_at: datetime | None
    error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ExternalIndexProbeEvaluation:
    verification_state: str
    error: str | None


@dataclass(frozen=True)
class ExternalSnapshotProbeEvaluation:
    compatible: bool
    evidence: dict[str, Any]
    error: str | None


def normalize_distance_metric(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return {
        "cosine": "cosine",
        "dot": "dot",
        "euclid": "euclid",
        "euclidean": "euclid",
        "manhattan": "manhattan",
    }.get(normalized, normalized)


def evaluate_external_index_probe(
    *,
    state: VectorIndexState,
    expected_dimension: int,
    expected_distance_metric: str,
    embedding_profile_attested: bool,
) -> ExternalIndexProbeEvaluation:
    """Classify one external collection probe without claiming unverifiable semantics.

    Qdrant can prove collection shape and distance, but it cannot prove which semantic
    embedding model created already-stored vectors. Therefore a matching structural
    probe remains ``unverified`` until an administrator explicitly attests the selected
    EmbeddingProfile.
    """

    if not state.exists:
        return ExternalIndexProbeEvaluation("unavailable", "External collection does not exist")

    status = (state.status or "").strip().lower()
    if status in {"red", "error", "failed", "dead", "unavailable"}:
        return ExternalIndexProbeEvaluation(
            "unavailable",
            f"External collection is not operational: status={state.status}",
        )

    if state.vector_type not in {None, "dense"}:
        return ExternalIndexProbeEvaluation(
            "incompatible",
            f"External collection vector type is unsupported: {state.vector_type}",
        )

    if state.dimension is None:
        return ExternalIndexProbeEvaluation(
            "incompatible",
            "External collection vector dimension cannot be determined",
        )
    if int(state.dimension) != int(expected_dimension):
        return ExternalIndexProbeEvaluation(
            "incompatible",
            "External collection vector dimension mismatch: "
            f"expected={expected_dimension}, actual={state.dimension}",
        )

    expected_distance = normalize_distance_metric(expected_distance_metric)
    observed_distance = normalize_distance_metric(state.distance_metric)
    if observed_distance is None:
        return ExternalIndexProbeEvaluation(
            "incompatible",
            "External collection distance metric cannot be determined",
        )
    if expected_distance != observed_distance:
        return ExternalIndexProbeEvaluation(
            "incompatible",
            "External collection distance metric mismatch: "
            f"expected={expected_distance}, actual={observed_distance}",
        )

    if not embedding_profile_attested:
        return ExternalIndexProbeEvaluation(
            "unverified",
            "EmbeddingProfile attestation is required because stored-vector model identity "
            "cannot be proven from Qdrant collection metadata alone",
        )

    return ExternalIndexProbeEvaluation("compatible", None)


def sample_payload_keys(samples: Iterable[VectorPointSample]) -> list[str]:
    keys: set[str] = set()
    for sample in samples:
        keys.update(str(key) for key in sample.payload.keys())
    return sorted(keys)


def _payload_content_hash(payload: Mapping[str, Any]) -> str | None:
    raw_hash = payload.get("content_sha256")
    if isinstance(raw_hash, str):
        normalized = raw_hash.strip().lower()
        if len(normalized) == 64 and all(ch in "0123456789abcdef" for ch in normalized):
            return normalized
    content = payload.get("content")
    if isinstance(content, str):
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
    return None


def evaluate_external_snapshot_probe(
    *,
    snapshot_id: str,
    project_id: str,
    selector: Mapping[str, Any],
    samples: list[VectorPointSample],
    snapshot_entries: list[Mapping[str, Any]],
    selector_points_count: int,
) -> ExternalSnapshotProbeEvaluation:
    """Evaluate whether sampled external payloads can be tied to one immutable Snapshot.

    A logical VectorIndex selector containing the exact ``snapshot_id`` is considered
    strong evidence because every count/sample operation is scoped by that Qdrant
    payload filter. Otherwise every sampled point must independently match the Snapshot
    by explicit snapshot_id or by content/path hash. A partial sample match is never
    promoted to a verified binding.
    """

    selector_dict = dict(selector)
    selector_snapshot_match = selector_dict.get("snapshot_id") == snapshot_id

    evidence: dict[str, Any] = {
        "selector_points_count": int(selector_points_count),
        "sample_size": len(samples),
        "selector_snapshot_match": selector_snapshot_match,
        "sample_snapshot_matches": 0,
        "sample_hash_matches": 0,
        "sample_project_matches": 0,
        "sample_payload_keys": sample_payload_keys(samples),
    }

    if selector_points_count < 1:
        return ExternalSnapshotProbeEvaluation(
            False,
            evidence,
            "External VectorIndex selector resolves to no points",
        )
    if not samples:
        return ExternalSnapshotProbeEvaluation(
            False,
            evidence,
            "External VectorIndex returned no payload samples",
        )

    snapshot_hashes = {
        str(entry.get("content_sha256") or "").strip().lower()
        for entry in snapshot_entries
        if str(entry.get("content_sha256") or "").strip()
    }
    path_hashes = {
        str(entry.get("relative_path") or ""): str(entry.get("content_sha256") or "").strip().lower()
        for entry in snapshot_entries
        if str(entry.get("relative_path") or "").strip()
        and str(entry.get("content_sha256") or "").strip()
    }

    point_proofs = 0
    for sample in samples:
        payload = sample.payload
        payload_project = payload.get("project_id")
        if payload_project is not None:
            if str(payload_project) != project_id:
                return ExternalSnapshotProbeEvaluation(
                    False,
                    evidence,
                    "External payload project_id does not match the Snapshot project",
                )
            evidence["sample_project_matches"] += 1

        payload_snapshot = payload.get("snapshot_id")
        snapshot_match = payload_snapshot is not None and str(payload_snapshot) == snapshot_id
        if payload_snapshot is not None and not snapshot_match:
            return ExternalSnapshotProbeEvaluation(
                False,
                evidence,
                "External payload snapshot_id does not match the requested Snapshot",
            )
        if snapshot_match:
            evidence["sample_snapshot_matches"] += 1

        payload_hash = _payload_content_hash(payload)
        path = str(payload.get("path") or "")
        hash_match = False
        if payload_hash:
            if path and path in path_hashes:
                hash_match = path_hashes[path] == payload_hash
            elif payload_hash in snapshot_hashes:
                hash_match = True
        if hash_match:
            evidence["sample_hash_matches"] += 1

        if selector_snapshot_match or snapshot_match or hash_match:
            point_proofs += 1

    if point_proofs != len(samples):
        return ExternalSnapshotProbeEvaluation(
            False,
            evidence,
            "Every sampled external point must be attributable to the Snapshot; "
            f"proved={point_proofs}, sampled={len(samples)}",
        )

    evidence["proof_mode"] = (
        "selector_snapshot_id"
        if selector_snapshot_match
        else (
            "payload_snapshot_id"
            if evidence["sample_snapshot_matches"] == len(samples)
            else "snapshot_content_hash"
        )
    )
    return ExternalSnapshotProbeEvaluation(True, evidence, None)


class PostgresExternalVectorIndexVerificationStore:
    """Current verification projection for externally owned VectorIndexes."""

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
                raise ExternalVectorIndexVerificationStoreError(
                    "PostgreSQL schema is not on the required Alembic revision"
                ) from exc

    @staticmethod
    def _columns() -> str:
        return (
            "vector_index_id, tenant_id, verification_state, verification_method, "
            "embedding_profile_attested, expected_dimension, observed_dimension, "
            "expected_distance_metric, observed_distance_metric, observed_vector_type, "
            "observed_points_count, selector_points_count, sample_size, "
            "sample_payload_keys, last_verified_at, error, created_at, updated_at"
        )

    @staticmethod
    def _record(row: dict[str, Any]) -> ExternalVectorIndexVerificationRecord:
        payload_keys = row.get("sample_payload_keys")
        return ExternalVectorIndexVerificationRecord(
            vector_index_id=str(row["vector_index_id"]),
            tenant_id=str(row["tenant_id"]),
            verification_state=str(row["verification_state"]),
            verification_method=str(row["verification_method"]),
            embedding_profile_attested=bool(row["embedding_profile_attested"]),
            expected_dimension=int(row["expected_dimension"]),
            observed_dimension=(
                int(row["observed_dimension"]) if row.get("observed_dimension") is not None else None
            ),
            expected_distance_metric=str(row["expected_distance_metric"]),
            observed_distance_metric=(
                str(row["observed_distance_metric"])
                if row.get("observed_distance_metric")
                else None
            ),
            observed_vector_type=(
                str(row["observed_vector_type"]) if row.get("observed_vector_type") else None
            ),
            observed_points_count=(
                int(row["observed_points_count"])
                if row.get("observed_points_count") is not None
                else None
            ),
            selector_points_count=(
                int(row["selector_points_count"])
                if row.get("selector_points_count") is not None
                else None
            ),
            sample_size=int(row.get("sample_size") or 0),
            sample_payload_keys=(
                [str(value) for value in payload_keys]
                if isinstance(payload_keys, list)
                else []
            ),
            last_verified_at=row.get("last_verified_at"),
            error=(str(row["error"]) if row.get("error") else None),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def upsert_probe(
        self,
        *,
        vector_index_id: str,
        tenant_id: str,
        verification_state: str,
        embedding_profile_attested: bool,
        expected_dimension: int,
        observed_dimension: int | None,
        expected_distance_metric: str,
        observed_distance_metric: str | None,
        observed_vector_type: str | None,
        observed_points_count: int | None,
        selector_points_count: int | None,
        sample_size: int,
        sample_payload_keys: list[str],
        error: str | None,
        checked: bool = True,
    ) -> ExternalVectorIndexVerificationRecord:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    f"""
                    INSERT INTO external_vector_index_verifications (
                        vector_index_id, tenant_id, verification_state, verification_method,
                        embedding_profile_attested, expected_dimension, observed_dimension,
                        expected_distance_metric, observed_distance_metric, observed_vector_type,
                        observed_points_count, selector_points_count, sample_size,
                        sample_payload_keys, last_verified_at, error, created_at, updated_at
                    ) VALUES (
                        %s,%s,%s,'qdrant_probe',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        CASE WHEN %s THEN NOW() ELSE NULL END,%s,NOW(),NOW()
                    )
                    ON CONFLICT (vector_index_id) DO UPDATE SET
                        tenant_id=EXCLUDED.tenant_id,
                        verification_state=EXCLUDED.verification_state,
                        verification_method='qdrant_probe',
                        embedding_profile_attested=EXCLUDED.embedding_profile_attested,
                        expected_dimension=EXCLUDED.expected_dimension,
                        observed_dimension=EXCLUDED.observed_dimension,
                        expected_distance_metric=EXCLUDED.expected_distance_metric,
                        observed_distance_metric=EXCLUDED.observed_distance_metric,
                        observed_vector_type=EXCLUDED.observed_vector_type,
                        observed_points_count=EXCLUDED.observed_points_count,
                        selector_points_count=EXCLUDED.selector_points_count,
                        sample_size=EXCLUDED.sample_size,
                        sample_payload_keys=EXCLUDED.sample_payload_keys,
                        last_verified_at=EXCLUDED.last_verified_at,
                        error=EXCLUDED.error,
                        updated_at=NOW()
                    RETURNING {self._columns()}
                    """,
                    (
                        vector_index_id,
                        tenant_id,
                        verification_state,
                        bool(embedding_profile_attested),
                        int(expected_dimension),
                        observed_dimension,
                        normalize_distance_metric(expected_distance_metric) or expected_distance_metric,
                        normalize_distance_metric(observed_distance_metric),
                        observed_vector_type,
                        observed_points_count,
                        selector_points_count,
                        int(sample_size),
                        Jsonb(sorted(set(sample_payload_keys))),
                        bool(checked),
                        error,
                    ),
                ).fetchone()
            if row is None:
                raise ExternalVectorIndexVerificationStoreError(
                    "External VectorIndex verification upsert returned no row"
                )
            return self._record(row)
        except ExternalVectorIndexVerificationStoreError:
            raise
        except (psycopg.Error, OSError) as exc:
            raise ExternalVectorIndexVerificationStoreError(
                "External VectorIndex verification write failed"
            ) from exc

    def get(self, vector_index_id: str) -> ExternalVectorIndexVerificationRecord | None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    f"SELECT {self._columns()} FROM external_vector_index_verifications WHERE vector_index_id=%s",
                    (vector_index_id,),
                ).fetchone()
            return self._record(row) if row else None
        except (psycopg.Error, OSError) as exc:
            raise ExternalVectorIndexVerificationStoreError(
                "External VectorIndex verification lookup failed"
            ) from exc

    def list(self, *, tenant_id: str | None = None) -> list[ExternalVectorIndexVerificationRecord]:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                if tenant_id:
                    rows = connection.execute(
                        f"SELECT {self._columns()} FROM external_vector_index_verifications WHERE tenant_id=%s ORDER BY updated_at DESC, vector_index_id",
                        (tenant_id,),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        f"SELECT {self._columns()} FROM external_vector_index_verifications ORDER BY updated_at DESC, vector_index_id"
                    ).fetchall()
            return [self._record(row) for row in rows]
        except (psycopg.Error, OSError) as exc:
            raise ExternalVectorIndexVerificationStoreError(
                "External VectorIndex verification list failed"
            ) from exc

