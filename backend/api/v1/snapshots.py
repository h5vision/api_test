from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Request

from ...contracts.snapshots import SnapshotCompareRequest, SnapshotCompareResponse


def create_snapshots_router(
    *,
    error_responses: dict[int, Any],
    compare_snapshot_handler: Callable[..., SnapshotCompareResponse],
) -> APIRouter:
    """Own the public Snapshot comparison route while its service is migrated separately."""

    router = APIRouter()

    @router.post(
        "/v1/snapshots/compare",
        response_model=SnapshotCompareResponse,
        tags=["Projects"],
        summary="Compare a Frontend project identity with the Backend Snapshot baseline",
        description=(
            "Resolve project_id with commit_id or snapshot_id and return same, different, "
            "or unknown. A different result sets update_warning=true. Textual None/null "
            "values are normalized to an omitted optional identity."
        ),
        responses=error_responses,
    )
    def compare_snapshot(
        payload: SnapshotCompareRequest,
        request: Request,
    ) -> SnapshotCompareResponse:
        return compare_snapshot_handler(payload, request)

    return router
