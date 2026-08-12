from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel

from backend.api.v1.admin import admin_route_keys, create_admin_router


ROOT = Path(__file__).resolve().parents[1]


class _AdminResponse(BaseModel):
    ok: bool


def _admin_handler() -> _AdminResponse:
    return _AdminResponse(ok=True)


def _public_handler() -> dict[str, bool]:
    return {"ok": True}


def test_admin_router_captures_only_admin_routes_and_preserves_route_objects() -> None:
    source = FastAPI()
    source.add_api_route(
        "/v1/admin/example",
        _admin_handler,
        methods=["GET"],
        response_model=_AdminResponse,
        status_code=202,
        include_in_schema=False,
        summary="Frozen admin route",
    )
    source.add_api_route(
        "/v1/public/example",
        _public_handler,
        methods=["GET"],
    )

    original = next(
        route
        for route in source.router.routes
        if isinstance(route, APIRoute) and route.path == "/v1/admin/example"
    )
    router = create_admin_router(source.router.routes)

    assert len(router.routes) == 1
    captured = router.routes[0]
    assert captured is original
    assert captured.response_model is _AdminResponse
    assert captured.status_code == 202
    assert captured.include_in_schema is False
    assert captured.summary == "Frozen admin route"
    assert admin_route_keys(router) == {("GET", "/v1/admin/example")}

    source.router.routes[:] = [
        route
        for route in source.router.routes
        if not (
            isinstance(route, APIRoute) and route.path == "/v1/admin/example"
        )
    ]
    source.router.routes.extend(router.routes)

    restored = [
        route
        for route in source.router.routes
        if isinstance(route, APIRoute) and route.path == "/v1/admin/example"
    ]
    assert restored == [original]
    assert restored[0] is original


class _IncludedRouterLike:
    def __init__(self, original_router: APIRouter) -> None:
        self.original_router = original_router


def test_admin_router_flattens_included_router_without_cloning_routes() -> None:
    nested = APIRouter(prefix="/v1/admin/snapshots")
    nested.add_api_route("/status", _admin_handler, methods=["GET"])
    nested.add_api_route("/import", _admin_handler, methods=["POST"])

    originals = list(nested.routes)
    wrapper = _IncludedRouterLike(nested)
    router = create_admin_router([wrapper])  # type: ignore[list-item]

    assert router.routes == originals
    assert router.routes[0] is originals[0]
    assert router.routes[1] is originals[1]
    assert admin_route_keys(router) == {
        ("GET", "/v1/admin/snapshots/status"),
        ("POST", "/v1/admin/snapshots/import"),
    }


def test_app_captures_admin_routes_before_legacy_removal() -> None:
    app_source = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")
    router_source = (ROOT / "backend" / "api" / "v1" / "admin.py").read_text(
        encoding="utf-8"
    )

    capture = "_admin_router = create_admin_router(_legacy_app.app.router.routes)"
    remove = "\n_remove_legacy_routes(\n"

    assert "from .api.v1.admin import admin_route_keys, create_admin_router" in app_source
    assert capture in app_source
    assert app_source.index(capture) < app_source.index(remove)
    assert "| admin_route_keys(_admin_router)" in app_source
    assert "_legacy_app.app.router.routes.extend(_admin_router.routes)" in app_source
    assert "_legacy_app.app.include_router(_admin_router)" not in app_source
    assert "original_router" in router_source
    assert "original_router" in app_source
    assert "Cannot partially remove a nested legacy router" in app_source
    assert "legacy_app" not in router_source
