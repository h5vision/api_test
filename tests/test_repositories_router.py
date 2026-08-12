from __future__ import annotations

from fastapi.routing import APIRoute

from backend.api.v1.repositories import create_repositories_router
from backend.contracts.repositories import (
    IndexingJobListResponse,
    IngestResponse,
    RepositoryBrowserListResponse,
)


def _handler(*args, **kwargs):
    return None


def test_repository_router_preserves_public_routes():
    router = create_repositories_router(
        list_repository_browser_items_handler=_handler,
        get_repository_source_tree_handler=_handler,
        ingest_documents_handler=_handler,
        ingest_documents_with_project_metadata_handler=_handler,
        create_upload_handler=_handler,
        add_upload_manifest_handler=_handler,
        upload_file_part_handler=_handler,
        get_upload_handler=_handler,
        complete_upload_handler=_handler,
        list_indexing_jobs_handler=_handler,
        get_indexing_job_handler=_handler,
        cancel_upload_handler=_handler,
    )
    routes = {
        (next(iter(route.methods)), route.path): route
        for route in router.routes
        if isinstance(route, APIRoute)
    }

    assert set(routes) == {
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
    }
    assert routes[("POST", "/v1/uploads")].status_code == 201
    assert routes[("POST", "/v1/uploads/{upload_id}/complete")].status_code == 202
    assert routes[("DELETE", "/v1/uploads/{upload_id}")].status_code == 204
    assert routes[("GET", "/v1/repositories")].response_model is RepositoryBrowserListResponse
    assert routes[("GET", "/v1/indexing-jobs")].response_model is IndexingJobListResponse
    assert routes[("POST", "/v1/documents/ingest")].response_model is IngestResponse
