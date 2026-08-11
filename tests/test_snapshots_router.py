from __future__ import annotations

from fastapi.routing import APIRoute

from backend.api.v1.snapshots import create_snapshots_router
from backend.contracts.snapshots import SnapshotCompareResponse


def _unused(*args: object, **kwargs: object) -> object:
    raise AssertionError("route execution is outside this contract test")


def test_snapshot_compare_route_preserves_public_contract() -> None:
    router = create_snapshots_router(
        error_responses={},
        compare_snapshot_handler=_unused,  # type: ignore[arg-type]
    )
    routes = [route for route in router.routes if isinstance(route, APIRoute)]
    assert len(routes) == 1
    route = routes[0]
    assert route.path == "/v1/snapshots/compare"
    assert route.methods == {"POST"}
    assert route.response_model is SnapshotCompareResponse
    assert route.tags == ["Projects"]
    assert route.summary == "Compare a Frontend project identity with the Backend Snapshot baseline"
