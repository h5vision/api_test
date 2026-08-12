from __future__ import annotations

from collections.abc import Iterable

from fastapi import APIRouter
from fastapi.routing import APIRoute
from starlette.routing import BaseRoute


ADMIN_PATH_PREFIX = "/v1/admin/"


def is_admin_route(route: BaseRoute) -> bool:
    """Return whether one existing FastAPI route belongs to the Admin API surface."""
    return isinstance(route, APIRoute) and route.path.startswith(ADMIN_PATH_PREFIX)


def _iter_route_tree(source_routes: Iterable[BaseRoute]) -> Iterable[BaseRoute]:
    """Yield concrete routes from direct and FastAPI included-router nodes.

    FastAPI 0.137+ preserves included routers as tree nodes instead of flattening
    their APIRoute objects into the parent route list.  Avoid importing the private
    _IncludedRouter type; its stable structural signal is the original_router
    reference that owns the nested ``routes`` collection.
    """
    for route in source_routes:
        if isinstance(route, APIRoute):
            yield route
            continue

        original_router = getattr(route, "original_router", None)
        nested_routes = getattr(original_router, "routes", None)
        if nested_routes is not None:
            yield from _iter_route_tree(nested_routes)


def create_admin_router(source_routes: Iterable[BaseRoute]) -> APIRouter:
    """Own existing Admin routes without redefining their FastAPI contracts.

    Phase 12 intentionally reuses the already-frozen APIRoute objects so response
    models, status codes, parameter constraints, summaries, schema visibility, and
    legacy handler callables remain unchanged while route ownership moves out of
    the monolithic application composition.
    """
    router = APIRouter()
    router.routes.extend(
        route for route in _iter_route_tree(source_routes) if is_admin_route(route)
    )
    return router


def admin_route_keys(router: APIRouter) -> set[tuple[str, str]]:
    """Return exact method/path keys used to remove captured routes from legacy."""
    return {
        (method, route.path)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in (route.methods or set())
    }
