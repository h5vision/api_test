from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_p2j_readiness_verifier_checks_route_chain_tenant_and_audit_continuity():
    text = (ROOT / "tools" / "verify_p2j_vector_federation.py").read_text(
        encoding="utf-8"
    )
    assert "require_schema(connection)" in text
    assert '"invalid_active_route_chain"' in text
    assert '"configured_tenant_violation"' in text
    assert '"incomplete_route_audit"' in text
    assert '"broken_route_event_transition"' in text
    assert '"route_head_event_mismatch"' in text
    assert "svb.tenant_id <> ps.tenant_id" in text
    assert "ig.vector_index_id <> svb.vector_index_id" in text
    assert "vi.selector ->> 'generation_id' <> svb.generation_id" in text


def test_p2j_runtime_rechecks_configured_tenant_and_locks_mutation_candidates():
    text = (ROOT / "backend" / "project_vector_routes.py").read_text(
        encoding="utf-8"
    )
    assert "candidate is outside the configured tenant" in text
    assert "FOR SHARE OF svb, ps, vi, vt, ep" in text
    assert text.count("_candidate_context(connection, binding_id, lock=True)") == 2


def test_legacy_active_generation_is_not_a_runtime_authority():
    route_store = (ROOT / "backend" / "project_vector_routes.py").read_text(
        encoding="utf-8"
    )
    app = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")
    runtime = app.split("def _resolve_project_vector_runtime", 1)[1].split(
        "def _search_documents_with_runtime", 1
    )[0]

    assert "active_generation_id" not in route_store
    assert "active_generation_id" not in runtime
