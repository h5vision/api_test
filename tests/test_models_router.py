from __future__ import annotations

from fastapi.routing import APIRoute

from backend.api.v1.models import create_models_router
from backend.contracts.models import ModelListResponse


class _UnusedGenerationRouter:
    """Only route registration is exercised; endpoint execution is covered elsewhere."""


def test_models_router_preserves_public_contract() -> None:
    router = create_models_router(_UnusedGenerationRouter())  # type: ignore[arg-type]
    routes = [route for route in router.routes if isinstance(route, APIRoute)]

    assert len(routes) == 1
    route = routes[0]
    assert route.path == "/v1/models"
    assert route.methods == {"GET"}
    assert route.response_model is ModelListResponse
    assert route.tags == ["System"]
    assert route.summary == "List selectable generation models"
