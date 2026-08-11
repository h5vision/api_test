from __future__ import annotations

from dataclasses import dataclass

from fastapi.routing import APIRoute

from backend.api.v1.system import create_system_router
from backend.contracts.system import LanguageDetectRequest


@dataclass
class _Setup:
    configured: bool = True
    missing: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


class _Settings:
    instance_id = "test-instance"

    def public_status(self) -> dict[str, object]:
        return {"mode": "test"}


class _Resolver:
    def setup_state(self, *, refresh: bool) -> _Setup:
        assert refresh is False
        return _Setup()


class _VectorStore:
    def stats(self) -> dict[str, object]:
        return {"status": "ready"}


class _Store:
    def __init__(self, name: str) -> None:
        self.name = name

    def status(self) -> dict[str, object]:
        return {"status": "ready", "store": self.name}


class _Detection:
    def public_dict(self) -> dict[str, object]:
        return {"language_id": "python", "confidence": 1.0}


class _Registry:
    def catalog(self) -> dict[str, object]:
        return {"registry_revision": "test", "languages": []}

    def detect(self, **kwargs: object) -> _Detection:
        assert kwargs["explicit_language_id"] == "python"
        return _Detection()


class _VectorError(Exception):
    pass


def _router():
    return create_system_router(
        settings=_Settings(),
        runtime_settings_resolver=_Resolver(),
        vector_store=_VectorStore(),
        metadata_store=_Store("metadata"),
        project_store=_Store("project"),
        vector_store_error=_VectorError,
        language_registry_factory=_Registry,
    )


def test_system_router_preserves_public_paths() -> None:
    routes = {
        (next(iter(route.methods)), route.path): route
        for route in _router().routes
        if isinstance(route, APIRoute)
    }
    assert ("GET", "/v1/health") in routes
    assert ("GET", "/v1/languages") in routes
    assert ("POST", "/v1/languages/detect") in routes
    assert routes[("GET", "/v1/languages")].summary == "List the VS Code-compatible language registry"
    assert routes[("POST", "/v1/languages/detect")].summary == "Detect or normalize one VS Code document language"


def test_health_payload_preserves_runtime_shape() -> None:
    route = next(route for route in _router().routes if isinstance(route, APIRoute) and route.path == "/v1/health")
    payload = route.endpoint()
    assert payload["status"] == "ok"
    assert payload["version"] == "3.0.0"
    assert payload["instance_id"] == "test-instance"
    assert payload["vector_store"] == {"status": "ready"}
    assert payload["message"] == "백엔드 API 서버에서 응답중 입니다."


def test_language_detection_contract_is_forwarded() -> None:
    route = next(route for route in _router().routes if isinstance(route, APIRoute) and route.path == "/v1/languages/detect")
    result = route.endpoint(LanguageDetectRequest(language_id="python"))
    assert result == {"language_id": "python", "confidence": 1.0}
