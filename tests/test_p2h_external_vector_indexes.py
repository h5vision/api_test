from pathlib import Path
import importlib
import sys
import types
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parents[1]


def test_p2h_migration_adds_external_verification_without_mutating_generation_contract():
    text = (ROOT / "migrations" / "versions" / "p2h_0007_external_vector_indexes.py").read_text(encoding="utf-8")
    assert 'revision = "p2h_0007_external_vector_indexes"' in text
    assert 'down_revision = "p2g_0006_snapshot_vector_bindings"' in text
    assert "CREATE TABLE IF NOT EXISTS external_vector_index_verifications" in text
    assert "embedding_profile_attested BOOLEAN" in text
    assert "ADD COLUMN IF NOT EXISTS verification_evidence JSONB" in text
    assert "ownership_mode = 'external_attached'" in text
    assert "IndexGeneration" not in text


def test_schema_guard_requires_p2h_and_keeps_new_table_out_of_historical_baseline():
    text = (ROOT / "backend" / "schema_guard.py").read_text(encoding="utf-8")
    assert 'CURRENT_REVISION = "p3_0009_chat_intake_normalization"' in text
    assert '"external_vector_index_verifications"' in text
    assert '"verification_evidence"' in text
    assert 'P2A_BASELINE_TABLE_COLUMNS.pop("external_vector_index_verifications", None)' in text


def test_vector_adapter_supports_discovery_without_registration():
    text = (ROOT / "backend" / "integrations" / "vectordb" / "vector_store.py").read_text(encoding="utf-8")
    assert "def discover_indexes(self) -> list[VectorIndexState]" in text
    assert 'self._request("GET", "/collections")' in text
    assert "self.describe_index(VectorIndexRef(collection=name))" in text


def test_external_attach_does_not_silently_change_embedding_space():
    text = (ROOT / "backend" / "domains" / "vector_indexes" / "vector_indexes.py").read_text(encoding="utf-8")
    block = text.split("def register_external", 1)[1].split("def register_generation", 1)[0]
    assert "different EmbeddingProfile" in block
    assert "cannot be reattached as external" in block
    assert "'external_attached'" in block
    assert "'unavailable'" in block


def test_admin_workflow_separates_discover_attach_verify_and_snapshot_binding():
    app = (ROOT / "backend" / "legacy_app.py").read_text(encoding="utf-8")
    assert '"/v1/admin/vector-targets/{vector_target_id}/indexes/discover"' in app
    assert '"/v1/admin/vector-indexes/attach"' in app
    assert '"/v1/admin/vector-indexes/{vector_index_id}/verify"' in app
    assert '"/v1/admin/snapshot-vector-bindings/external/verify"' in app
    assert "embedding_profile_attested" in app
    assert "snapshot_attested" in app


def test_external_binding_has_no_generation_and_persists_verification_evidence():
    text = (ROOT / "backend" / "domains" / "vector_indexes" / "snapshot_vector_bindings.py").read_text(encoding="utf-8")
    block = text.split("def register_external_verification", 1)[1].split("def get(", 1)[0]
    assert "generation_id" in block
    assert "NULL,'external_verification','verified'" in block
    assert "verification_evidence" in block
    assert "external_vector_index_verifications" in block


def _load_external_module():
    # Load pure P2-H evaluators while stubbing DB/runtime-only imports.
    psycopg = types.ModuleType("psycopg")
    psycopg.Error = Exception
    psycopg.Connection = object
    psycopg.connect = lambda **kwargs: None
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = object()
    types_mod = types.ModuleType("psycopg.types")
    json_mod = types.ModuleType("psycopg.types.json")
    json_mod.Jsonb = lambda value: value
    sys.modules["psycopg"] = psycopg
    sys.modules["psycopg.rows"] = rows
    sys.modules["psycopg.types"] = types_mod
    sys.modules["psycopg.types.json"] = json_mod

    package = types.ModuleType("backend")
    package.__path__ = [str(ROOT / "backend")]
    sys.modules["backend"] = package

    config = types.ModuleType("backend.config")
    config.Settings = object
    sys.modules["backend.config"] = config

    guard = types.ModuleType("backend.schema_guard")
    guard.SchemaStateError = RuntimeError
    guard.require_schema = lambda connection: None
    sys.modules["backend.schema_guard"] = guard

    vector_store = types.ModuleType("backend.vector_store")

    @dataclass(frozen=True)
    class VectorIndexState:
        exists: bool
        collection: str
        dimension: int | None
        distance_metric: str | None
        vector_type: str | None
        points_count: int | None
        status: string

    @dataclass(frozen=True)
    class VectorPointSample:
        point_id: str
        payload: dict
        vector: list | None = None

    vector_store.VectorIndexState = VectorIndexState
    vector_store.VectorPointSample = VectorPointSample
    sys.modules["backend.vector_store"] = vector_store

    sys.modules.pop("backend.external_vector_indexes", None)
    return importlib.import_module("backend.external_vector_indexes"), VectorIndexState, VectorPointSample


def test_structural_probe_requires_profile_attestation_and_rejects_dimension_mismatch():
    module, VectorIndexState, _ = _load_external_module()
    state = VectorIndexState(True, "c", 1024, "cosine", "dense", 10, "green")
    unverified = module.evaluate_external_index_probe(
        state=state,
        expected_dimension=1024,
        expected_distance_metric="cosine",
        embedding_profile_attested=False,
    )
    assert unverified.verification_state == "unverified"
    compatible = module.evaluate_external_index_probe(
        state=state,
        expected_dimension=1024,
        expected_distance_metric="cosine",
        embedding_profile_attested=True,
    )
    assert compatible.verification_state == "compatible"
    mismatch = module.evaluate_external_index_probe(
        state=state,
        expected_dimension=768,
        expected_distance_metric="cosine",
        embedding_profile_attested=True,
    )
    assert mismatch.verification_state == "incompatible"


def test_snapshot_probe_accepts_exact_snapshot_selector_and_rejects_mixed_payload():
    module, _, VectorPointSample = _load_external_module()
    samples = [
        VectorPointSample("1", {"snapshot_id": "snap-a", "project_id": "p", "content": "a"}),
        VectorPointSample("2", {"snapshot_id": "snap-a", "project_id": "p", "content": "b"}),
    ]
    result = module.evaluate_external_snapshot_probe(
        snapshot_id="snap-a",
        project_id="p",
        selector={"snapshot_id": "snap-a"},
        samples=samples,
        snapshot_entries=[],
        selector_points_count=2,
    )
    assert result.compatible is True
    assert result.evidence["proof_mode"] == "selector_snapshot_id"

    mixed = [
        samples[0],
        VectorPointSample("2", {"snapshot_id": "snap-b", "project_id": "p", "content": "b"}),
    ]
    rejected = module.evaluate_external_snapshot_probe(
        snapshot_id="snap-a",
        project_id="p",
        selector={},
        samples=mixed,
        snapshot_entries=[],
        selector_points_count=2,
    )
    assert rejected.compatible is False
    assert "snapshot_id" in (rejected.error or "")


def test_reattach_preserves_existing_verification_and_external_binding_checks_tenant():
    app = (ROOT / "backend" / "legacy_app.py").read_text(encoding="utf-8")
    attach = app.split("def attach_external_vector_index", 1)[1].split("def get_external_vector_index_verification", 1)[0]
    assert "external_vector_index_verification_store.get(index.vector_index_id)" in attach
    assert "if verification is None:" in attach

    bindings = (ROOT / "backend" / "domains" / "vector_indexes" / "snapshot_vector_bindings.py").read_text(encoding="utf-8")
    block = bindings.split("def register_external_verification", 1)[1].split("def get(", 1)[0]
    assert "vi.tenant_id AS vector_tenant_id" in block
    assert "Snapshot and external VectorIndex tenant boundaries do not match" in block
