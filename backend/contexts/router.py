from __future__ import annotations


from functools import lru_cache


from fastapi import APIRouter, HTTPException, Response, status


from ..config import settings
from ..snapshots.repository import PostgresSnapshotRepository
from ..vector_indexes.registry import PostgresVectorIndexRegistry
from .registry import (
    ContextBindingError,
    ContextCreate,
    ContextExpiredError,
    ContextRecord,
    ContextService,
    PostgresContextStore,
    SnapshotVectorMismatchError,
)




router = APIRouter(
    prefix="/v1/snapshot-control/contexts",
    tags=["AI Context Selector"],
)




@lru_cache(maxsize=1)
def _service() -> ContextService:
    return ContextService(
        PostgresSnapshotRepository(settings),
        PostgresVectorIndexRegistry(settings),
        PostgresContextStore(settings),
    )




def _error(exc: ContextBindingError) -> HTTPException:
    if isinstance(exc, SnapshotVectorMismatchError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": exc.code,
                "requested_snapshot_id": exc.requested_snapshot_id,
                "indexed_snapshot_id": exc.indexed_snapshot_id,
                "vector_index_id": exc.vector_index_id,
                "action": "reindex_required",
            },
        )
    if isinstance(exc, ContextExpiredError):
        return HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"code": exc.code, "message": str(exc)},
        )
    if "not found" in str(exc).lower():
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": exc.code, "message": str(exc)},
        )
    if "different" in str(exc).lower() or "not ready" in str(exc).lower():
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": str(exc)},
        )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": exc.code, "message": str(exc)},
    )




@router.post("", response_model=ContextRecord, status_code=status.HTTP_201_CREATED)
def create_context(payload: ContextCreate) -> ContextRecord:
    try:
        return _service().create(payload)
    except ContextBindingError as exc:
        raise _error(exc) from exc




@router.get("/{context_id}", response_model=ContextRecord)
def resolve_context(context_id: str) -> ContextRecord:
    try:
        return _service().resolve(context_id)
    except ContextBindingError as exc:
        raise _error(exc) from exc




@router.delete("/{context_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_context(context_id: str) -> Response:
    try:
        deleted = _service().delete(context_id)
    except ContextBindingError as exc:
        raise _error(exc) from exc
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CONTEXT_NOT_FOUND", "context_id": context_id},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)