from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, status

from ...contracts.repositories import (
    IndexingJobListResponse,
    IndexingJobResponse,
    IngestResponse,
    RepositoryBrowserListResponse,
    RepositoryIndexJobResponse,
    RepositorySourceTreeResponse,
    UploadProgressResponse,
    UploadSessionResponse,
)


def create_repositories_router(
    *,
    list_repository_browser_items_handler: Callable[..., Any],
    get_repository_source_tree_handler: Callable[..., Any],
    ingest_documents_handler: Callable[..., Any],
    ingest_documents_with_project_metadata_handler: Callable[..., Any],
    create_upload_handler: Callable[..., Any],
    add_upload_manifest_handler: Callable[..., Any],
    upload_file_part_handler: Callable[..., Any],
    get_upload_handler: Callable[..., Any],
    complete_upload_handler: Callable[..., Any],
    list_indexing_jobs_handler: Callable[..., Any],
    get_indexing_job_handler: Callable[..., Any],
    cancel_upload_handler: Callable[..., Any],
) -> APIRouter:
    """Own public repository, indexing, and upload routes without changing handlers."""

    router = APIRouter()
    router.add_api_route(
        "/v1/repositories",
        list_repository_browser_items_handler,
        methods=["GET"],
        response_model=RepositoryBrowserListResponse,
        tags=["Projects"],
        summary="List Git sources and compare source HEAD with the active DB snapshot",
    )
    router.add_api_route(
        "/v1/repositories/{source_id}/tree",
        get_repository_source_tree_handler,
        methods=["GET"],
        response_model=RepositorySourceTreeResponse,
        tags=["Projects"],
        summary="Read the tracked folder tree from the Backend Git checkout HEAD",
    )
    router.add_api_route(
        "/v1/documents/ingest",
        ingest_documents_handler,
        methods=["POST"],
        response_model=IngestResponse,
        tags=["Documents"],
    )
    router.add_api_route(
        "/v1/documents/ingest-with-metadata",
        ingest_documents_with_project_metadata_handler,
        methods=["POST"],
        response_model=IngestResponse,
        tags=["Documents"],
    )
    router.add_api_route(
        "/v1/uploads",
        create_upload_handler,
        methods=["POST"],
        response_model=UploadSessionResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Uploads"],
    )
    router.add_api_route(
        "/v1/uploads/{upload_id}/manifest",
        add_upload_manifest_handler,
        methods=["POST"],
        response_model=UploadProgressResponse,
        tags=["Uploads"],
    )
    router.add_api_route(
        "/v1/uploads/{upload_id}/files/{file_id}/parts/{part_number}",
        upload_file_part_handler,
        methods=["PUT"],
    )
    router.add_api_route(
        "/v1/uploads/{upload_id}",
        get_upload_handler,
        methods=["GET"],
        response_model=UploadProgressResponse,
    )
    router.add_api_route(
        "/v1/uploads/{upload_id}/complete",
        complete_upload_handler,
        methods=["POST"],
        response_model=IndexingJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    router.add_api_route(
        "/v1/indexing-jobs",
        list_indexing_jobs_handler,
        methods=["GET"],
        response_model=IndexingJobListResponse,
        tags=["Documents"],
        summary="List repository and Frontend upload indexing progress",
    )
    router.add_api_route(
        "/v1/indexing-jobs/{job_id}",
        get_indexing_job_handler,
        methods=["GET"],
        response_model=UploadProgressResponse | RepositoryIndexJobResponse,
        tags=["Documents"],
        summary="Read one indexing job",
    )
    router.add_api_route(
        "/v1/uploads/{upload_id}",
        cancel_upload_handler,
        methods=["DELETE"],
        status_code=status.HTTP_204_NO_CONTENT,
    )
    return router
