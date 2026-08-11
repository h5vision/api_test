from __future__ import annotations

import sys

from fastapi.routing import APIRoute

from . import legacy_app as _legacy_app
from .api.v1.models import create_models_router
from .api.v1.system import create_system_router
from .api.v1.projects import create_projects_router
from .api.v1.snapshots import create_snapshots_router


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
        ("GET", "/v1/IngestResponse"),
        ("GET", "/v1/projects/{project_id:path}/briefing"),
        ("GET", "/v1/briefing"),
        ("GET", "/v1/projects/{project_id:path}/tree"),
        ("GET", "/v1/projects/{project_id:path}/file"),
        ("GET", "/v1/projects/{project_id}/metadata"),
        ("POST", "/v1/projects/{project_id}/version/check"),
        ("POST", "/v1/snapshots/compare"),
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

_legacy_app.app.include_router(
    create_projects_router(
        error_responses=_legacy_app.ERROR_RESPONSES,
        list_indexed_projects_handler=_legacy_app.list_indexed_projects,
        project_briefing_handler=_legacy_app.get_project_briefing,
        project_briefing_compatibility_handler=_legacy_app.get_project_briefing_compatibility,
        project_tree_handler=_legacy_app.get_project_tree,
        project_file_handler=_legacy_app.get_project_file,
        project_metadata_handler=_legacy_app.list_project_metadata,
        project_version_handler=_legacy_app.check_project_version,
    )
)

_legacy_app.app.include_router(
    create_snapshots_router(
        error_responses=_legacy_app.ERROR_RESPONSES,
        compare_snapshot_handler=_legacy_app.compare_snapshot,
    )
)

# Keep historical import and monkeypatch targets stable while the monolith is
# carved into domain-owned routers.
sys.modules[__name__] = _legacy_app
