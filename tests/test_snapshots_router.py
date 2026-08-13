from __future__ import annotations

from fastapi.routing import APIRoute

from backend.api.v1.snapshots import create_snapshots_router
from backend.contracts.snapshots import SnapshotCompareResponse, WorkspaceOverlayResponse
from backend.domains.snapshots.hydration import SnapshotHydrationService


class _HydrationRepo:
    def get_snapshot(self, snapshot_id: str, tenant_id: str):
        return None

    def list_entries(self, snapshot_id: str, tenant_id: str):
        return []

    def get_entry(self, snapshot_id: str, tenant_id: str, path: str):
        return None


class _OverlayService:
    def create(self, payload: object) -> object:
        raise AssertionError("route execution is outside this contract test")


def _unused(*args: object, **kwargs: object) -> object:
    raise AssertionError("route execution is outside this contract test")


def _router():
    return create_snapshots_router(
        error_responses={},
        compare_snapshot_handler=_unused,  # type: ignore[arg-type]
        hydration_service=SnapshotHydrationService(
            _HydrationRepo(), tenant_id="vision-default", token="x" * 40
        ),
        overlay_service=_OverlayService(),  # type: ignore[arg-type]
    )


def test_snapshot_compare_route_preserves_public_contract() -> None:
    routes = [route for route in _router().routes if isinstance(route, APIRoute)]
    route = next(route for route in routes if route.path == "/v1/snapshots/compare")
    assert route.methods == {"POST"}
    assert route.response_model is SnapshotCompareResponse
    assert route.tags == ["Projects"]
    assert route.summary == "Compare a Frontend project identity with the Backend Snapshot baseline"


def test_snapshot_hydration_routes_are_owned_by_snapshot_router() -> None:
    routes = [route for route in _router().routes if isinstance(route, APIRoute)]
    keys = {(method, route.path) for route in routes for method in (route.methods or set())}
    assert keys == {
        ("POST", "/v1/snapshots/compare"),
        ("POST", "/v1/workspace-overlays"),
        ("GET", "/v1/snapshot-hydrations/{snapshot_id}"),
        ("GET", "/v1/snapshot-hydrations/{snapshot_id}/manifest"),
        ("GET", "/v1/snapshot-hydrations/{snapshot_id}/file"),
    }


def test_workspace_overlay_route_is_backend_owned_and_returns_created() -> None:
    routes = [route for route in _router().routes if isinstance(route, APIRoute)]
    route = next(route for route in routes if route.path == "/v1/workspace-overlays")
    assert route.methods == {"POST"}
    assert route.status_code == 201
    assert route.response_model is WorkspaceOverlayResponse
