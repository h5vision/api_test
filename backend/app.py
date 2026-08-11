from __future__ import annotations

import sys

from fastapi.routing import APIRoute

from . import legacy_app as _legacy_app
from .api.v1.models import create_models_router


def _replace_models_route() -> None:
    """Replace only the legacy public model catalog route during migration."""
    _legacy_app.app.router.routes[:] = [
        route
        for route in _legacy_app.app.router.routes
        if not (
            isinstance(route, APIRoute)
            and route.path == "/v1/models"
            and "GET" in (route.methods or set())
        )
    ]
    _legacy_app.app.include_router(create_models_router(_legacy_app.generation_router))


_replace_models_route()

# Preserve the historical ``backend.app`` module surface for existing tests,
# imports, monkeypatch targets, and runtime integrations while route ownership
# is moved out incrementally.
sys.modules[__name__] = _legacy_app
