from __future__ import annotations


import hmac
import os
from functools import lru_cache
from typing import Annotated


from fastapi import APIRouter, Depends, Header, HTTPException, Query, status


from ..config import settings
from .adapters.github import GithubAdapterError
from .contracts import (
    AccessPlan,
    RepositoryCreateRequest,
    RepositoryRecord,
    SnapshotCreateRequest,
    SnapshotFileResponse,
    SnapshotListResponse,
    SnapshotRecord,
    SnapshotRegistrationResponse,
    SnapshotTreeResponse,
)
from .service import GithubSnapshotService, GithubSnapshotServiceError




SNAPSHOT_TOKEN_HEADER = "X-Vision-Snapshot-Token"
MIN_SNAPSHOT_TOKEN_BYTES = 32



def require_snapshot_token(
    token: Annotated[
        str | None,
        Header(alias=SNAPSHOT_TOKEN_HEADER),
    ] = None,
) -> None:
    expected = os.getenv("SNAPSHOT_MVP_TOKEN", "").strip()
    if len(expected.encode("utf-8")) < MIN_SNAPSHOT_TOKEN_BYTES:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Snapshot MVP authentication is not securely configured",
        )
    supplied = (token or "").strip()
    if not supplied:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Snapshot authentication is required",
            headers={"WWW-Authenticate": "VisionSnapshotToken"},
        )
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Snapshot authentication failed",
        )




@lru_cache(maxsize=1)
def get_snapshot_service() -> GithubSnapshotService:
    return GithubSnapshotService(settings)




def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, GithubSnapshotServiceError):
        return HTTPException(status_code=exc.status_code, detail=str(exc))
    if isinstance(exc, GithubAdapterError):
        return HTTPException(status_code=exc.status_code, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Snapshot Control Plane request failed",
    )




router = APIRouter(
    prefix="/v1/snapshot-control",
    tags=["Snapshot Control Plane"],
    dependencies=[Depends(require_snapshot_token)],
)




@router.post(
    "/repositories",
    response_model=RepositoryRecord,
    status_code=status.HTTP_201_CREATED,
    summary="Register one allowlisted public GitHub repository",
)
def register_repository(
    payload: RepositoryCreateRequest,
    service: Annotated[GithubSnapshotService, Depends(get_snapshot_service)],
) -> RepositoryRecord:
    try:
        return service.register_repository(payload.repository_full_name)
    except Exception as exc:
        raise _translate_error(exc) from exc




@router.get(
    "/repositories/{repository_id}",
    response_model=RepositoryRecord,
    summary="Read one registered GitHub repository",
)
def get_repository(
    repository_id: str,
    service: Annotated[GithubSnapshotService, Depends(get_snapshot_service)],
) -> RepositoryRecord:
    try:
        return service.get_repository(repository_id)
    except Exception as exc:
        raise _translate_error(exc) from exc




@router.post(
    "/repositories/{repository_id}/snapshots",
    response_model=SnapshotRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Resolve a GitHub ref and register its verified immutable Commit Snapshot",
)
def create_snapshot(
    repository_id: str,
    payload: SnapshotCreateRequest,
    service: Annotated[GithubSnapshotService, Depends(get_snapshot_service)],
) -> SnapshotRegistrationResponse:
    try:
        return service.create_snapshot(repository_id, payload.ref)
    except Exception as exc:
        raise _translate_error(exc) from exc




@router.get(
    "/repositories/{repository_id}/snapshots",
    response_model=SnapshotListResponse,
    summary="List immutable Commit Snapshots for one repository",
)
def list_snapshots(
    repository_id: str,
    service: Annotated[GithubSnapshotService, Depends(get_snapshot_service)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> SnapshotListResponse:
    try:
        return service.list_snapshots(repository_id, limit=limit)
    except Exception as exc:
        raise _translate_error(exc) from exc




@router.get(
    "/snapshots/{snapshot_id}",
    response_model=SnapshotRecord,
    summary="Read one immutable Commit Snapshot",
)
def get_snapshot(
    snapshot_id: str,
    service: Annotated[GithubSnapshotService, Depends(get_snapshot_service)],
) -> SnapshotRecord:
    try:
        return service.get_snapshot(snapshot_id)
    except Exception as exc:
        raise _translate_error(exc) from exc




@router.get(
    "/snapshots/{snapshot_id}/resolve",
    response_model=AccessPlan,
    summary="Resolve the verified GitHub backend-proxy Access Plan",
)
def resolve_snapshot(
    snapshot_id: str,
    service: Annotated[GithubSnapshotService, Depends(get_snapshot_service)],
) -> AccessPlan:
    try:
        return service.resolve(snapshot_id)
    except Exception as exc:
        raise _translate_error(exc) from exc




@router.get(
    "/snapshots/{snapshot_id}/tree",
    response_model=SnapshotTreeResponse,
    summary="Read the recursive Git tree fixed to the Snapshot tree SHA",
)
def get_snapshot_tree(
    snapshot_id: str,
    service: Annotated[GithubSnapshotService, Depends(get_snapshot_service)],
) -> SnapshotTreeResponse:
    try:
        return service.tree(snapshot_id)
    except Exception as exc:
        raise _translate_error(exc) from exc




@router.get(
    "/snapshots/{snapshot_id}/file",
    response_model=SnapshotFileResponse,
    summary="Read one UTF-8 file fixed to the Snapshot commit SHA",
)
def get_snapshot_file(
    snapshot_id: str,
    path: Annotated[str, Query(min_length=1, max_length=4096)],
    service: Annotated[GithubSnapshotService, Depends(get_snapshot_service)],
) -> SnapshotFileResponse:
    try:
        return service.file(snapshot_id, path)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise _translate_error(exc) from exc
