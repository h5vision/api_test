from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Header, HTTPException, Query, Request, status

from ...contracts.snapshots import (
    SnapshotCompareRequest,
    SnapshotCompareResponse,
    SnapshotHydrationFile,
    SnapshotHydrationInfo,
    SnapshotHydrationManifestPage,
    WorkspaceOverlayRequest,
    WorkspaceOverlayResponse,
)
from ...domains.snapshots.hydration import (
    HYDRATION_TOKEN_HEADER,
    SnapshotHydrationError,
    SnapshotHydrationService,
)
from ...domains.snapshots.workspace_overlay import (
    WorkspaceOverlayError,
    WorkspaceOverlayService,
)


def create_snapshots_router(
    *,
    error_responses: dict[int, Any],
    compare_snapshot_handler: Callable[..., SnapshotCompareResponse],
    hydration_service: SnapshotHydrationService | None = None,
    overlay_service: WorkspaceOverlayService | None = None,
) -> APIRouter:
    """Own Snapshot comparison plus the read-only Snapshot hydration facade."""

    router = APIRouter()
    hydration = hydration_service or _default_hydration_service()
    overlays = overlay_service or _default_overlay_service()

    @router.post(
        "/v1/snapshots/compare",
        response_model=SnapshotCompareResponse,
        tags=["Projects"],
        summary="Compare a Frontend project identity with the Backend Snapshot baseline",
        description=(
            "Resolve project_id with commit_id or snapshot_id and return same, different, "
            "or unknown. Revision equality remains independent from optional working-tree "
            "state so a matching HEAD can still report a modified workspace."
        ),
        responses=error_responses,
    )
    def compare_snapshot(
        payload: SnapshotCompareRequest,
        request: Request,
    ) -> SnapshotCompareResponse:
        return compare_snapshot_handler(payload, request)

    @router.post(
        "/v1/workspace-overlays",
        response_model=WorkspaceOverlayResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Projects"],
        summary="Materialize Frontend workspace files as a Backend-owned Snapshot",
        description=(
            "Accept real Git commit SHA values plus final full file text. The Backend "
            "computes content hashes, the manifest, and snapshot_id; Frontend must not "
            "invent revision or content hash values."
        ),
        responses=error_responses,
    )
    def create_workspace_overlay(
        payload: WorkspaceOverlayRequest,
    ) -> WorkspaceOverlayResponse:
        try:
            return overlays.create(payload)
        except WorkspaceOverlayError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @router.get(
        "/v1/snapshot-hydrations/{snapshot_id}",
        response_model=SnapshotHydrationInfo,
        tags=["Projects"],
        summary="Read a Snapshot hydration descriptor",
    )
    def get_snapshot_hydration(
        snapshot_id: str,
        hydration_token: str | None = Header(default=None, alias=HYDRATION_TOKEN_HEADER),
    ) -> SnapshotHydrationInfo:
        _authorize_hydration(hydration, hydration_token)
        return _hydration_call(hydration.info, snapshot_id)

    @router.get(
        "/v1/snapshot-hydrations/{snapshot_id}/manifest",
        response_model=SnapshotHydrationManifestPage,
        tags=["Projects"],
        summary="Read one stable page of the Snapshot hydration manifest",
    )
    def get_snapshot_hydration_manifest(
        snapshot_id: str,
        cursor: str | None = Query(default=None, max_length=4096),
        limit: int = Query(default=500, ge=1, le=2000),
        hydration_token: str | None = Header(default=None, alias=HYDRATION_TOKEN_HEADER),
    ) -> SnapshotHydrationManifestPage:
        _authorize_hydration(hydration, hydration_token)
        return _hydration_call(
            hydration.manifest,
            snapshot_id,
            cursor=cursor,
            limit=limit,
        )

    @router.get(
        "/v1/snapshot-hydrations/{snapshot_id}/file",
        response_model=SnapshotHydrationFile,
        tags=["Projects"],
        summary="Read exact materialized text from one Snapshot file",
    )
    def get_snapshot_hydration_file(
        snapshot_id: str,
        path: str = Query(..., min_length=1, max_length=4096),
        hydration_token: str | None = Header(default=None, alias=HYDRATION_TOKEN_HEADER),
    ) -> SnapshotHydrationFile:
        _authorize_hydration(hydration, hydration_token)
        return _hydration_call(hydration.file, snapshot_id, path)

    return router


def _authorize_hydration(
    service: SnapshotHydrationService,
    supplied: str | None,
) -> None:
    try:
        service.authorize(supplied)
    except SnapshotHydrationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _hydration_call(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return function(*args, **kwargs)
    except SnapshotHydrationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _default_hydration_service() -> SnapshotHydrationService:
    # Import deployment settings lazily so router contract tests and canonical
    # module imports do not need to initialize unrelated runtime configuration.
    from ...config import settings as bootstrap_settings

    return SnapshotHydrationService.from_settings(bootstrap_settings)


def _default_overlay_service() -> WorkspaceOverlayService:
    from ...config import settings as bootstrap_settings

    return WorkspaceOverlayService.from_settings(bootstrap_settings)
