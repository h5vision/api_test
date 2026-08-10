from backend.embedding_profiles import (
    embedding_profile_identity,
    normalize_embedding_base_url,
)


def _identity(*, batch_size=16, dimension=1536):
    # batch_size is deliberately not accepted by the identity function.
    return embedding_profile_identity(
        "vision-default",
        deployment="api",
        provider="openai",
        base_url="https://embedding.example/v1/",
        model="embed-model",
        model_id="embed-model-v1",
        dimension=dimension,
    )


def test_embedding_base_url_normalization():
    assert normalize_embedding_base_url("https://embedding.example/v1/") == "https://embedding.example/v1"


def test_embedding_profile_identity_is_stable_and_dimension_sensitive():
    assert _identity() == _identity(batch_size=64)
    assert _identity(dimension=1536) != _identity(dimension=1024)


def test_embedding_profile_identity_is_tenant_scoped():
    first = _identity()
    second = embedding_profile_identity(
        "another-tenant",
        deployment="api",
        provider="openai",
        base_url="https://embedding.example/v1",
        model="embed-model",
        model_id="embed-model-v1",
        dimension=1536,
    )
    assert first != second

