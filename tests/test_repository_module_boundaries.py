from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_repository_implementations_have_canonical_domain_ownership():
    for name in ("repository_store","repository_indexer","uploads","offline_embeddings"):
        canonical=ROOT/"backend"/"domains"/"repositories"/f"{name}.py"; shim=ROOT/"backend"/f"{name}.py"
        assert canonical.exists() and canonical.stat().st_size>1000
        text=shim.read_text(encoding="utf-8"); assert f"domains.repositories import {name}" in text; assert "sys.modules[__name__] = _implementation" in text
def test_repository_indexer_uses_domain_vector_bridges():
    package=ROOT/"backend"/"domains"/"repositories"
    assert "vector_indexes.vector_store" in (package/"vector_store.py").read_text(encoding="utf-8")
    assert "vector_indexes.vector_indexes" in (package/"vector_indexes.py").read_text(encoding="utf-8")
    assert "vector_indexes.project_vector_routes" in (package/"project_vector_routes.py").read_text(encoding="utf-8")
    assert "vector_indexes.snapshot_vector_bindings" in (package/"snapshot_vector_bindings.py").read_text(encoding="utf-8")
def test_metadata_store_remains_outside_repository_phase():
    assert not (ROOT/"backend"/"domains"/"repositories"/"metadata_store.py").exists()
