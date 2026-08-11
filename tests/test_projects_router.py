from __future__ import annotations

from fastapi.routing import APIRoute

from backend.api.v1.projects import create_projects_router
from backend.contracts.projects import (
    IndexedProjectListResponse,
    MetadataListResponse,
    ProjectBriefingResponse,
    ProjectFileResponse,
    ProjectTreeResponse,
    ProjectVersionCheckResponse,
)


def _unused(*args: object, **kwargs: object) -> object:
    raise AssertionError("route execution is outside this contract test")


def _router():
    return create_projects_router(
        error_responses={},
        list_indexed_projects_handler=_unused,
        project_briefing_handler=_unused,
        project_briefing_compatibility_handler=_unused,
        project_tree_handler=_unused,
        project_file_handler=_unused,
        project_metadata_handler=_unused,
        project_version_handler=_unused,
    )


def test_projects_router_preserves_public_contracts() -> None:
    routes = {
        (next(iter(route.methods)), route.path): route
        for route in _router().routes
        if isinstance(route, APIRoute)
    }
    expected = {
        ("GET", "/v1/IngestResponse"): IndexedProjectListResponse,
        ("GET", "/v1/projects/{project_id:path}/briefing"): ProjectBriefingResponse,
        ("GET", "/v1/briefing"): ProjectBriefingResponse,
        ("GET", "/v1/projects/{project_id:path}/tree"): ProjectTreeResponse,
        ("GET", "/v1/projects/{project_id:path}/file"): ProjectFileResponse,
        ("GET", "/v1/projects/{project_id}/metadata"): MetadataListResponse,
        ("POST", "/v1/projects/{project_id}/version/check"): ProjectVersionCheckResponse,
    }
    assert set(routes) == set(expected)
    for key, response_model in expected.items():
        assert routes[key].response_model is response_model


def test_project_route_tags_remain_compatible() -> None:
    routes = {
        route.path: route
        for route in _router().routes
        if isinstance(route, APIRoute)
    }
    assert routes["/v1/IngestResponse"].tags == ["Projects"]
    assert routes["/v1/projects/{project_id:path}/briefing"].tags == ["Projects"]
    assert routes["/v1/projects/{project_id}/version/check"].tags == ["Documents"]
