from datetime import datetime, timezone

from backend.config import Settings
from backend.embedding_profiles import EmbeddingProfileRecord
from backend.runtime_authority import RuntimeSettingsProxy, RuntimeSettingsResolver
from backend.runtime_config import NetworkEndpoint, RuntimeNetworkSettings
from backend.runtime_services import RuntimeGroqSettings, RuntimeServiceSettings, RuntimeVectorSettings
from backend.vector_targets import VectorTargetRecord


class ServiceStore:
    def __init__(self, value): self.value = value
    def get(self, *, refresh=False): return self.value
    def cached(self): return self.value
    @staticmethod
    def is_complete(value):
        return bool(value and value.vector.vector_target_id and value.vector.embedding_profile_id and value.vector.collection and value.vector.index_version and value.groq.default_model_id)


class NetworkStore:
    def __init__(self, value): self.value = value
    def get(self, *, refresh=False): return self.value
    def cached(self): return self.value
    def configured(self, *, refresh=False): return self.value is not None


class TargetStore:
    def get(self, target_id):
        if target_id != "vtarget_admin": return None
        now = datetime.now(timezone.utc)
        return VectorTargetRecord(
            vector_target_id=target_id, tenant_id="vision-default", name="Admin Qdrant",
            engine="qdrant", endpoint="https://qdrant-admin.example", credential_ref=None,
            deployment_type="remote_server", capabilities={}, status="configured",
            error=None, latency_ms=None, last_checked_at=None, created_at=now, updated_at=now,
        )


class ProfileStore:
    def __init__(self, model="embed-admin", model_id="embed-profile-admin"):
        self.model = model; self.model_id = model_id
    def get(self, profile_id):
        if profile_id != "eprof_admin": return None
        now = datetime.now(timezone.utc)
        return EmbeddingProfileRecord(
            embedding_profile_id=profile_id, tenant_id="vision-default", name="Admin Embed",
            deployment="api", provider="openai", base_url="https://embedding.example/v1",
            model=self.model, model_id=self.model_id, dimension=1536, batch_size=16,
            credential_ref=None, status="configured", error=None, latency_ms=None,
            last_checked_at=None, created_at=now, updated_at=now,
        )


def runtime_service(*, default="backendai:model-admin"):
    return RuntimeServiceSettings(
        groq=RuntimeGroqSettings(False, "https://model-api.example/v1", "cloud-admin", default),
        vector=RuntimeVectorSettings(
            vector_target_id="vtarget_admin", embedding_profile_id="eprof_admin",
            collection="admin_vectors", index_version="admin-v2",
        ),
    )


def test_registry_target_and_embedding_profile_are_runtime_authority():
    bootstrap = Settings.from_environment()
    resolver = RuntimeSettingsResolver(
        bootstrap, ServiceStore(runtime_service()),
        NetworkStore(RuntimeNetworkSettings(NetworkEndpoint("10.0.0.5", 8888), NetworkEndpoint("10.0.0.12", 9000))),
        TargetStore(), ProfileStore(), cache_ttl_seconds=0,
    )
    current = resolver.current(force=True)
    assert current.vector_target_id == "vtarget_admin"
    assert current.qdrant_url == "https://qdrant-admin.example"
    assert current.embedding_profile_id == "eprof_admin"
    assert current.embedding_model == "embed-admin"
    assert current.embedding_model_id == "embed-profile-admin"
    assert current.embedding_dimension == 1536
    assert current.backendai_base_url == "http://10.0.0.12:9000"


def test_proxy_sees_embedding_profile_change_without_restart():
    profile_store = ProfileStore(model="first", model_id="profile-first")
    resolver = RuntimeSettingsResolver(
        Settings.from_environment(), ServiceStore(runtime_service()),
        NetworkStore(RuntimeNetworkSettings(NetworkEndpoint("10.0.0.5", 8888), NetworkEndpoint("10.0.0.12", 9000))),
        TargetStore(), profile_store, cache_ttl_seconds=60,
    )
    proxy = RuntimeSettingsProxy(resolver)
    assert proxy.embedding_model == "first"
    profile_store.model = "second"; profile_store.model_id = "profile-second"
    resolver.invalidate()
    assert proxy.embedding_model == "second"
    assert proxy.embedding_model_id == "profile-second"

