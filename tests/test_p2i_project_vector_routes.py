from __future__ import annotations

import importlib
import sys
import types
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_p2i_migration_creates_route_authority_and_removes_generation_active_semantics():
    text = (ROOT / "migrations" / "versions" / "p2i_0008_project_vector_routes.py").read_text(encoding="utf-8")
    assert 'revision = "p2i_0008_project_vector_routes"' in text
    assert 'down_revision = "p2h_0007_external_vector_indexes"' in text
    assert "ADD COLUMN IF NOT EXISTS ready_at TIMESTAMPTZ" in text
    assert "SET status = 'ready'" in text
    assert "WHERE status = 'active'" in text
    assert "CREATE TABLE IF NOT EXISTS project_vector_routes" in text
    assert "active_binding_id TEXT NULL" in text
    assert "routing_mode IN ('managed_auto', 'pinned')" in text
    assert "CREATE TABLE IF NOT EXISTS project_vector_route_events" in text
    assert "projects.active_generation_id" in text
    assert "svb.verification_state = 'verified'" in text
    assert "vi.ownership_mode = 'vision_managed'" in text
    assert "ps.tenant_id = svb.tenant_id" in text
    assert "vi.tenant_id = svb.tenant_id" in text
    assert "vt.tenant_id = svb.tenant_id" in text
    assert "ep.tenant_id = svb.tenant_id" in text
    assert "vi.selector ->> 'project_id' = p.project_id" in text
    assert "vi.selector ->> 'generation_id' = ig.generation_id" in text
    assert "DROP COLUMN IF EXISTS active_generation_id" not in text


def test_schema_guard_requires_p2i_route_shape_without_rewriting_historical_baseline():
    text = (ROOT / "backend" / "schema_guard.py").read_text(encoding="utf-8")
    assert 'CURRENT_REVISION = "p3_0010_external_project_registry"' in text
    assert '"project_vector_routes"' in text
    assert '"project_vector_route_events"' in text
    assert '"ready_at"' in text
    assert 'P2A_BASELINE_TABLE_COLUMNS.pop("project_vector_routes", None)' in text
    assert 'P2A_BASELINE_TABLE_COLUMNS.pop("project_vector_route_events", None)' in text


def test_build_readiness_and_route_promotion_are_separate():
    store = (
        ROOT / "backend" / "domains" / "repositories" / "repository_store.py"
    ).read_text(encoding="utf-8")
    completion = store.split("def complete_generation", 1)[1].split("def fail_generation", 1)[0]
    assert "SET status = 'ready'" in completion
    assert "verification_state = 'verified'" in completion
    assert "active_generation_id =" not in completion
    assert "SET status = 'retired'" not in completion

    indexer = (
        ROOT / "backend" / "domains" / "repositories" / "repository_indexer.py"
    ).read_text(encoding="utf-8")
    offline = (
        ROOT / "backend" / "domains" / "repositories" / "offline_embeddings.py"
    ).read_text(encoding="utf-8")
    assert "completed_binding_id = self.store.complete_generation" in indexer
    assert "promote_managed_binding" in indexer
    assert indexer.index("complete_generation") < indexer.index("promote_managed_binding")
    assert "completed_binding_id = self.store.complete_generation" in offline
    assert "promote_managed_binding" in offline
    assert offline.index("complete_generation") < offline.index("promote_managed_binding")


def test_runtime_has_one_route_authority_and_no_active_generation_fallback():
    app = (ROOT / "backend" / "legacy_app.py").read_text(encoding="utf-8")
    block = app.split("def _resolve_project_vector_runtime", 1)[1].split(
        "def _search_documents_with_runtime", 1
    )[0]
    assert "project_vector_route_store.get(project_id)" in block
    assert "route.active_binding_id" in block
    assert "PROJECT_VECTOR_ROUTE_REQUIRED" in block
    assert "snapshot_vector_binding_store.get(route.active_binding_id)" in block
    assert "active_generation_id" not in block

    repository = (
        ROOT / "backend" / "domains" / "repositories" / "repository_store.py"
    ).read_text(encoding="utf-8")
    projection = repository.split("def get_active_generation", 1)[1].split(
        "def get_current_snapshot_context", 1
    )[0]
    assert "FROM project_vector_routes AS pvr" in projection
    assert "projects.active_generation_id" in projection  # documentation says it is not read
    assert "p.active_generation_id" not in projection


def test_persistent_selector_is_authoritative_for_managed_and_external_routes():
    gateway = (ROOT / "backend" / "domains" / "vector_indexes" / "vector_gateway.py").read_text(encoding="utf-8")
    block = gateway.split("def build_vector_store_for_index", 1)[1]
    assert "selector=index.selector" in block
    assert "query_selector_authoritative=True" in block

    vector_store = (ROOT / "backend" / "integrations" / "vectordb" / "vector_store.py").read_text(encoding="utf-8")
    search = vector_store.split("def search(", 2)[2].split("def count_generation", 1)[0]
    assert "if self.query_selector_authoritative" in search
    assert "operation_selector = VectorSelector()" in search
    assert "generation_id: str | None = None" in search


def test_admin_route_contract_uses_binding_id_and_optimistic_revision():
    schemas = (
        ROOT / "backend" / "contracts" / "vector.py"
    ).read_text(encoding="utf-8")
    app = (ROOT / "backend" / "legacy_app.py").read_text(encoding="utf-8")
    assert "class ProjectVectorRouteWriteRequest" in schemas
    assert "binding_id: str" in schemas
    assert 'routing_mode: Literal["managed_auto", "pinned"]' in schemas
    assert "expected_revision: int" in schemas
    assert '"/v1/admin/projects/{project_id:path}/vector-route"' in app
    assert '"/v1/admin/projects/{project_id:path}/vector-route/candidates"' in app
    assert '"/v1/admin/projects/{project_id:path}/vector-route/events"' in app
    assert "except ProjectVectorRouteConflict" in app
    assert "HTTP_409_CONFLICT" in app


def _load_route_module():
    psycopg = types.ModuleType("psycopg")
    psycopg.Error = Exception
    psycopg.Connection = object
    psycopg.connect = lambda **kwargs: None
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = object()
    sys.modules["psycopg"] = psycopg
    sys.modules["psycopg.rows"] = rows

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

    sys.modules.pop("backend.project_vector_routes", None)
    return importlib.import_module("backend.project_vector_routes")


def _managed_candidate(module):
    return module.RouteCandidateContext(
        binding_id="svb-managed",
        tenant_id="vision-default",
        snapshot_id="snap-1",
        snapshot_project_id="project-a",
        snapshot_status="completed",
        snapshot_tenant_id="vision-default",
        generation_id="gen-1",
        generation_status="ready",
        vector_index_id="vidx-1",
        vector_index_tenant_id="vision-default",
        ownership_mode="vision_managed",
        binding_source="managed_generation",
        binding_verification_state="verified",
        verification_method="managed_build",
        vector_target_id="vtarget-1",
        vector_target_tenant_id="vision-default",
        embedding_profile_id="eprof-1",
        embedding_profile_tenant_id="vision-default",
        vector_index_status="ready",
        vector_target_status="healthy",
        embedding_profile_status="healthy",
        external_verification_state=None,
        external_verification_tenant_id=None,
        sample_payload_keys=[],
    )


def test_managed_candidate_requires_ready_not_active_and_is_routable():
    module = _load_route_module()
    candidate = _managed_candidate(module)
    module.validate_route_candidate("project-a", candidate)
    assert module.candidate_runtime_routable(candidate) is True

    legacy_active = replace(candidate, generation_status="active")
    with pytest.raises(module.ProjectVectorRouteStoreError):
        module.validate_route_candidate("project-a", legacy_active)


def test_external_candidate_separates_compatibility_from_runtime_routability():
    module = _load_route_module()
    candidate = replace(
        _managed_candidate(module),
        binding_id="svb-external",
        generation_id=None,
        generation_status=None,
        ownership_mode="external_attached",
        binding_source="external_verification",
        verification_method="manual",
        external_verification_state="compatible",
        external_verification_tenant_id="vision-default",
        sample_payload_keys=["content", "document_id", "chunk_id"],
    )
    module.validate_route_candidate("project-a", candidate)
    assert module.candidate_runtime_routable(candidate) is True

    locator_only = replace(candidate, sample_payload_keys=["document_id", "chunk_id", "path"])
    module.validate_route_candidate("project-a", locator_only)
    assert module.candidate_runtime_routable(locator_only) is False


def test_cross_tenant_candidate_is_rejected():
    module = _load_route_module()
    candidate = replace(_managed_candidate(module), vector_index_tenant_id="other-tenant")
    with pytest.raises(module.ProjectVectorRouteStoreError):
        module.validate_route_candidate("project-a", candidate)


def test_candidate_with_internally_consistent_but_unconfigured_tenant_is_rejected():
    module = _load_route_module()
    candidate = replace(
        _managed_candidate(module),
        tenant_id="other-tenant",
        snapshot_tenant_id="other-tenant",
        vector_index_tenant_id="other-tenant",
        vector_target_tenant_id="other-tenant",
        embedding_profile_tenant_id="other-tenant",
    )
    store = module.PostgresProjectVectorRouteStore(
        SimpleNamespace(snapshot_tenant_id="vision-default")
    )

    with pytest.raises(module.ProjectVectorRouteStoreError, match="configured tenant"):
        store.validate_candidate("project-a", candidate)


def test_route_mutations_lock_candidate_rows_and_external_verification_tenant():
    text = (ROOT / "backend" / "domains" / "vector_indexes" / "project_vector_routes.py").read_text(encoding="utf-8")
    assert 'lock_clause = " FOR SHARE OF svb, ps, vi, vt, ep" if lock else ""' in text
    assert "self._candidate_context(connection, binding_id, lock=True)" in text
    assert "AND ev.tenant_id = svb.tenant_id" in text
