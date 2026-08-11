from __future__ import annotations

import sys

from fastapi.routing import APIRoute

from . import legacy_app as _legacy_app
from .api.v1.models import create_models_router
from .api.v1.system import create_system_router


def _remove_legacy_routes(routes: set[tuple[str, str]]) -> None:
    """Remove only routes whose ownership has moved out of the legacy module."""
    _legacy_app.app.router.routes[:] = [
        route
        for route in _legacy_app.app.router.routes
        if not (
            isinstance(route, APIRoute)
            and any(
                route.path == path and method in (route.methods or set())
                for method, path in routes
            )
        )
    ]


_remove_legacy_routes(
    {
        ("GET", "/v1/models"),
        ("GET", "/v1/health"),
        ("GET", "/v1/languages"),
        ("POST", "/v1/languages/detect"),
    }
)

_legacy_app.app.include_router(create_models_router(_legacy_app.generation_router))
_legacy_app.app.include_router(
    create_system_router(
        settings=_legacy_app.settings,
        runtime_settings_resolver=_legacy_app.runtime_settings_resolver,
        vector_store=_legacy_app.vector_store,
        metadata_store=_legacy_app.metadata_store,
        project_store=_legacy_app.project_store,
        vector_store_error=_legacy_app.VectorStoreError,
        language_registry_factory=_legacy_app.language_registry,
    )
)

# Keep historical import and monkeypatch targets stable while the monolith is
# carved into domain-owned routers.
sys.modules[__name__] = _legacy_app
