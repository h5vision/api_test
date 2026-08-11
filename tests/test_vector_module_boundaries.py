from backend import embedding_profiles as le, external_vector_indexes as lx, project_vector_routes as lr, snapshot_vector_bindings as lb, vector_contract as lc, vector_gateway as lg, vector_indexes as li, vector_store as ls, vector_targets as lt
from backend.domains.vector_indexes import embedding_profiles as e, external_vector_indexes as x, project_vector_routes as r, snapshot_vector_bindings as b, vector_contract as c, vector_gateway as g, vector_indexes as i, vector_targets as t
from backend.integrations.vectordb import vector_store as s

def test_vector_module_boundaries_preserve_public_identity():
    assert li.PostgresVectorIndexStore is i.PostgresVectorIndexStore
    assert lt.PostgresVectorTargetStore is t.PostgresVectorTargetStore
    assert le.PostgresEmbeddingProfileStore is e.PostgresEmbeddingProfileStore
    assert lb.PostgresSnapshotVectorBindingStore is b.PostgresSnapshotVectorBindingStore
    assert lr.PostgresProjectVectorRouteStore is r.PostgresProjectVectorRouteStore
    assert lx.evaluate_external_index_probe is x.evaluate_external_index_probe
    assert lg.build_vector_store is g.build_vector_store
    assert lc.vector_service_contract is c.vector_service_contract
    assert ls.QdrantVectorAdapter is s.QdrantVectorAdapter
