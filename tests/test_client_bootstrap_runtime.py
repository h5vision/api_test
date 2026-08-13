from pathlib import Path
from types import SimpleNamespace

from backend.contracts.admin import NetworkSettingsUpdateRequest
from backend.runtime_config import (
    NetworkEndpoint,
    PostgresRuntimeNetworkSettingsStore,
    RuntimeNetworkSettings,
)


ROOT = Path(__file__).resolve().parents[1]


def test_backendai_network_settings_no_longer_require_a_frontend_endpoint() -> None:
    payload = NetworkSettingsUpdateRequest.model_validate(
        {"backendai": {"ip": "192.168.0.12", "port": 11500}}
    )

    assert payload.frontend is None
    assert payload.backendai.ip == "192.168.0.12"
    assert payload.backendai.port == 11500


def test_network_setup_readiness_uses_the_outbound_ai_target_only() -> None:
    store = PostgresRuntimeNetworkSettingsStore(SimpleNamespace())  # type: ignore[arg-type]
    store.get = lambda **_kwargs: RuntimeNetworkSettings(  # type: ignore[method-assign]
        frontend=NetworkEndpoint("", 0),
        backendai=NetworkEndpoint("192.168.0.12", 11500),
    )

    assert store.configured(refresh=True) is True


def test_client_auto_enrolment_precedes_runtime_capability_guard() -> None:
    source = (ROOT / "backend" / "legacy_app.py").read_text(encoding="utf-8")

    registration = source.index(
        "if managed_frontend_request and not client_guard_exempt:"
    )
    runtime_guard = source.index("runtime_guard_exempt = (")

    assert registration < runtime_guard
    assert '"/v1/chat",' in source[runtime_guard:]
    assert '"/v1/chat/contexts",' in source[runtime_guard:]
    assert '"/v1/snapshots/compare",' in source[runtime_guard:]


def test_public_model_and_language_discovery_do_not_require_registration() -> None:
    source = (ROOT / "backend" / "legacy_app.py").read_text(encoding="utf-8")

    public_discovery = source.index("public_discovery_request = (")
    registration = source.index(
        "if managed_frontend_request and not client_guard_exempt:"
    )
    discovery_contract = source[public_discovery:registration]

    assert '"/v1/models"' in discovery_contract
    assert '"/v1/languages"' in discovery_contract
    assert '"/v1/languages/detect"' in discovery_contract
    assert "or public_discovery_request" in discovery_contract
