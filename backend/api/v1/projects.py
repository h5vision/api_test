from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

from ...contracts.projects import (
    IndexedProjectListResponse,
    MetadataListResponse,
    MetadataScope,
    ProjectBriefingResponse,
    ProjectFileResponse,
    ProjectTreeResponse,
    ProjectVersionCheckRequest,
    ProjectVersionCheckResponse,
)


def create_projects_router(
    *,
    error_responses: dict[int, Any],
    list_indexed_projects_handler: Callable[..., Any],
    project_briefing_handler: Callable[..., Any],
    project_briefing_compatibility_handler: Callable[..., Any],
    project_tree_handler: Callable[..., Any],
    project_file_handler: Callable[..., Any],
    project_metadata_handler: Callable[..., Any],
    project_version_handler: Callable[..., Any],
) -> APIRouter:
    """Own public project routes while legacy services remain injected during migration."""

    router = APIRouter()

    @router.get(
        "/v1/IngestResponse",
        response_model=IndexedProjectListResponse,
        tags=["Projects"],
        summary="List backend projects and indexed Git versions",
        description=(
            "Used when the VS Code Extension starts and when the user refreshes "
            "the Sidebar project list."
        ),
        responses=error_responses,
    )
    def list_indexed_projects(
        request: Request,
        response: Response,
    ) -> IndexedProjectListResponse | JSONResponse:
        return list_indexed_projects_handler(request, response)

    @router.get(
        "/v1/projects/{project_id:path}/briefing",
        response_model=ProjectBriefingResponse,
        responses=error_responses,
        tags=["Projects"],
        summary="Read the generated external RAG project briefing",
    )
    def get_project_briefing(
        project_id: str,
        request: Request,
        commit_id: str | None = Query(default=None, min_length=7, max_length=64),
    ) -> ProjectBriefingResponse:
        return project_briefing_handler(project_id, request, commit_id)

    @router.get(
        "/v1/briefing",
        response_model=ProjectBriefingResponse,
        responses=error_responses,
        tags=["Projects"],
        summary="Compatibility route for the generated project briefing",
    )
    def get_project_briefing_compatibility(
        request: Request,
        project_id: str = Query(..., min_length=1, max_length=255),
        commit_id: str | None = Query(default=None, min_length=7, max_length=64),
    ) -> ProjectBriefingResponse:
        return project_briefing_compatibility_handler(request, project_id, commit_id)

    @router.get(
        "/v1/projects/{project_id:path}/tree",
        response_model=ProjectTreeResponse,
        tags=["Projects"],
        summary="Read the active backend snapshot folder tree",
    )
    def get_project_tree(
        project_id: str,
        path: str = Query(default="", max_length=4096),
    ) -> ProjectTreeResponse:
        return project_tree_handler(project_id, path)

    @router.get(
        "/v1/projects/{project_id:path}/file",
        response_model=ProjectFileResponse,
        tags=["Projects"],
        summary="Read text from the active backend snapshot",
    )
    def get_project_file(
        project_id: str,
        path: str = Query(..., min_length=1, max_length=4096),
    ) -> ProjectFileResponse:
        return project_file_handler(project_id, path)

    @router.get(
        "/v1/projects/{project_id}/metadata",
        response_model=MetadataListResponse,
    )
    def list_project_metadata(
        project_id: str,
        scope: MetadataScope | None = None,
        limit: int = Query(default=5000, ge=1, le=10000),
    ) -> MetadataListResponse:
        return project_metadata_handler(project_id, scope, limit)

    @router.post(
        "/v1/projects/{project_id}/version/check",
        response_model=ProjectVersionCheckResponse,
        tags=["Documents"],
        summary="Compare a local workspace version with the Backend DB registry",
        description=(
            "The VS Code extension sends the complete loaded workspace tree plus any "
            "snapshot, manifest hash, modified time, and Git information it has. "
            "Absolute client paths are ignored. The API compares the normalized tree "
            "with PROJECT_DB_LOCAL_ROOT/project_id and enriches it with PostgreSQL data."
        ),
    )
    def check_project_version(
        project_id: str,
        payload: ProjectVersionCheckRequest,
    ) -> ProjectVersionCheckResponse:
        return project_version_handler(project_id, payload)

    return router
