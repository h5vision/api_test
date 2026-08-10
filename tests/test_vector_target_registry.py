import hashlib

from backend.vector_targets import (
    infer_vector_target_deployment,
    normalize_vector_target_endpoint,
    vector_target_identity,
)


def test_endpoint_normalization_and_identity_are_stable_and_match_migration_material():
    endpoint = normalize_vector_target_endpoint("https://qdrant.example:6333/")
    assert endpoint == "https://qdrant.example:6333"
    identity = vector_target_identity("vision-default", "qdrant", endpoint)
    assert identity == vector_target_identity(
        "vision-default", "QDRANT", "https://qdrant.example:6333/"
    )
    material = "vision-default|qdrant|https://qdrant.example:6333"
    assert identity == f"vtarget_{hashlib.md5(material.encode('utf-8')).hexdigest()[:24]}"


def test_deployment_type_detection():
    assert infer_vector_target_deployment("http://qdrant:6333") == "local"
    assert infer_vector_target_deployment("http://qdrant.vision.svc:6333") == "cluster"
    assert infer_vector_target_deployment("https://vectors.example.com") == "remote_server"

