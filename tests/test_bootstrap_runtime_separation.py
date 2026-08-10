from datetime import datetime, timezone

from backend.config import Settings
from backend.embedding_profiles import EmbeddingProfileRecord
from backend.runtime_authority import RuntimeSettingsResolver
from backend.runtime_config import NetworkEndpoint, RuntimeNetworkSettings
from backend.runtime_services import RuntimeGroqSettings, RuntimeServiceSettings, RuntimeVectorSettings
from backend.vector_targets import VectorTargetRecord


class ServiceStore:
    def __init__(self, value=None): self.value = value
    def get(self, *, refresh=False): return self.value
    def cached(self): return self.value
    @staticmethod
    def is_complete(value):
        return bool(value and value.vector.vector_target_id and value.vector.embedding_profile_id and value.vector.collection and value.vector.index_version and value.groq.default_model_id)


class NetworkStore:
    def __init__(self, value=None): self.value = value
    def get(self, *, refresh=False): return self.value
    def cached(self): return self.value
    def configured(self, *, refresh=False): return self.value is not None


class TargetStore:
    def __init__(self, present=True): self.present = present
    def get(self, target_id):
        if not self.present or target_id != "vtarget_1": return None
        now = datetime.now(timezone.utc)
        return VectorTargetRecord(
            target_id, "vision-default", "Qdrant", "qdrant", "http://qdrant.example:6333",
            None, "remote_server", {}, "configured", None, None, None, now, now,
        )


class ProfileStore:
    def __init__(self, present=True): self.present = present
    def get(self, profile_id):
        if not self.present or profile_id != "eprof_1": return None
        now = datetime.now(timezone.utc)
        return EmbeddingProfileRecord(
            profile_id, "vision-default", "Embed", "api", "openai",
            "https://embedding.example/v1", "embed-a", "embed-a", 1536, 16,
            None, "configured", None, None, None, now, now,
        )


def configured_service():
    return RuntimeServiceSettings(
        groq=RuntimeGroqSettings(False, "https://models.example/v1", "model-a", "provider:example:model-a"),
        vector=RuntimeVectorSettings(
            vector_target_id="vtarget_1", embedding_profile_id="eprof_1",
            collection="vision_vectors", index_version="v2",
        ),
    )


def network():
    return RuntimeNetworkSettings(NetworkEndpoint("10.0.0.5", 8888), NetworkEndpoint("10.0.0.12", 9000))


def test_runtime_environment_variables_are_not_operational_fallbacks(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", "http://should-not-be-used:6333")
    monkeypatch.setenv("EMBEDDING_MODEL", "should-not-be-used")
    settings = Settings.from_environment()
    assert settings.vector_target_id == ""
    assert settings.embedding_profile_id == ""
    assert settings.qdrant_url == ""
    assert settings.embedding_provider == "unconfigured"


def test_missing_admin_rows_are_setup_required():
    resolver = RuntimeSettingsResolver(
        Settings.from_environment(), ServiceStore(), NetworkStore(), TargetStore(False), ProfileStore(False), cache_ttl_seconds=0,
    )
    state = resolver.setup_state(refresh=True)
    assert state.configured is False
    assert "network_settings" in state.missing
    assert "service_settings" in state.missing
    assert "vector_target" in state.missing
    assert "embedding_profile" in state.missing


def test_missing_selected_embedding_profile_is_setup_required():
    resolver = RuntimeSettingsResolver(
        Settings.from_environment(), ServiceStore(configured_service()), NetworkStore(network()),
        TargetStore(True), ProfileStore(False), cache_ttl_seconds=0,
    )
    state = resolver.setup_state(refresh=True)
    assert state.configured is False
    assert state.vector_target_configured is True
    assert state.embedding_profile_configured is False
    assert "embedding_profile" in state.missing
    assert resolver.current(force=True).embedding_profile_id == "eprof_1"
    assert resolver.current(force=True).embedding_model == ""


def test_admin_rows_and_both_registries_are_runtime_authority():
    resolver = RuntimeSettingsResolver(
        Settings.from_environment(), ServiceStore(configured_service()), NetworkStore(network()),
        TargetStore(True), ProfileStore(True), cache_ttl_seconds=0,
    )
    state = resolver.setup_state(refresh=True)
    current = resolver.current(force=True)
    assert state.configured is True
    assert state.vector_target_configured is True
    assert state.embedding_profile_configured is True
    assert current.qdrant_url == "http://qdrant.example:6333"
    assert current.embedding_model == "embed-a"

