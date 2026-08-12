from __future__ import annotations

import sys

from fastapi.routing import APIRoute

from . import legacy_app as _legacy_app
from .api.v1.models import create_models_router
from .api.v1.system import create_system_router
from .api.v1.projects import create_projects_router
from .api.v1.snapshots import create_snapshots_router
from .api.v1.repositories import create_repositories_router
from .api.v1.chat import create_chat_router
from .api.v1.admin import admin_route_keys, create_admin_router


def _route_method_path_keys(route: object) -> set[tuple[str, str]]:
    """Collect method/path keys from one direct or included-router route node."""
    if isinstance(route, APIRoute):
        return {(method, route.path) for method in (route.methods or set())}

    original_router = getattr(route, "original_router", None)
    nested_routes = getattr(original_router, "routes", None)
    if nested_routes is None:
        return set()

    keys: set[tuple[str, str]] = set()
    for nested_route in nested_routes:
        keys.update(_route_method_path_keys(nested_route))
    return keys


def _legacy_route_is_owned(route: object, routes: set[tuple[str, str]]) -> bool:
    """Return whether one legacy route node can be removed as a whole."""
    keys = _route_method_path_keys(route)
    overlap = keys & routes
    if not overlap:
        return False

    if isinstance(route, APIRoute):
        # Preserve the historical behavior: one matching method/path transfers the
        # concrete APIRoute out of legacy ownership.
        return True

    if overlap != keys:
        raise RuntimeError(
            "Cannot partially remove a nested legacy router; ownership is mixed"
        )
    return True


def _remove_legacy_routes(routes: set[tuple[str, str]]) -> None:
    """Remove only routes whose ownership has moved out of the legacy module."""
    _legacy_app.app.router.routes[:] = [
        route
        for route in _legacy_app.app.router.routes
        if not _legacy_route_is_owned(route, routes)
    ]


_admin_router = create_admin_router(_legacy_app.app.router.routes)

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
        ("GET", "/v1/repositories"),
        ("GET", "/v1/repositories/{source_id}/tree"),
        ("POST", "/v1/documents/ingest"),
        ("POST", "/v1/documents/ingest-with-metadata"),
        ("POST", "/v1/uploads"),
        ("POST", "/v1/uploads/{upload_id}/manifest"),
        ("PUT", "/v1/uploads/{upload_id}/files/{file_id}/parts/{part_number}"),
        ("GET", "/v1/uploads/{upload_id}"),
        ("POST", "/v1/uploads/{upload_id}/complete"),
        ("GET", "/v1/indexing-jobs"),
        ("GET", "/v1/indexing-jobs/{job_id}"),
        ("DELETE", "/v1/uploads/{upload_id}"),
        ("GET", "/v1/contracts/canonical-context"),
        ("POST", "/v1/chat/contexts"),
        ("GET", "/v1/chat/contexts/{context_id}"),
        ("GET", "/v1/contracts/chat-stream"),
        ("GET", "/v1/citations/{request_id}/{citation_id}"),
        ("POST", "/v1/chat"),
    }
    | admin_route_keys(_admin_router)
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

_legacy_app.app.include_router(
    create_chat_router(
        error_responses=_legacy_app.ERROR_RESPONSES,
        canonical_context_contract_handler=_legacy_app.canonical_context_contract,
        register_chat_context_handler=_legacy_app.register_chat_context,
        get_chat_context_handler=_legacy_app.get_chat_context,
        chat_stream_contract_handler=_legacy_app.chat_stream_contract,
        get_chat_citation_handler=_legacy_app.get_chat_citation,
        chat_handler=_legacy_app.chat,
    )
)

_legacy_app.app.include_router(
    create_repositories_router(
        list_repository_browser_items_handler=_legacy_app.list_repository_browser_items,
        get_repository_source_tree_handler=_legacy_app.get_repository_source_tree,
        ingest_documents_handler=_legacy_app.ingest_documents,
        ingest_documents_with_project_metadata_handler=_legacy_app.ingest_documents_with_project_metadata,
        create_upload_handler=_legacy_app.create_upload,
        add_upload_manifest_handler=_legacy_app.add_upload_manifest,
        upload_file_part_handler=_legacy_app.upload_file_part,
        get_upload_handler=_legacy_app.get_upload,
        complete_upload_handler=_legacy_app.complete_upload,
        list_indexing_jobs_handler=_legacy_app.list_indexing_jobs,
        get_indexing_job_handler=_legacy_app.get_indexing_job,
        cancel_upload_handler=_legacy_app.cancel_upload,
    )
)

_legacy_app.app.router.routes.extend(_admin_router.routes)

# Keep historical import and monkeypatch targets stable while the monolith is
# carved into domain-owned routers.
sys.modules[__name__] = _legacy_app
