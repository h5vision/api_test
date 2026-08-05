from __future__ import annotations


from functools import lru_cache


from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel


from ..config import settings
from .registry import (
    PostgresVectorIndexRegistry,
    VectorIndexCreate,
    VectorIndexFreshness,
    VectorIndexRecord,
    VectorIndexRegistryError,
    VectorIndexStatus,
)




class VectorIndexRegisterResponse(BaseModel):
    vector_index: VectorIndexRecord
    deduplicated: bool




class VectorIndexStatusUpdate(BaseModel):
    status: VectorIndexStatus
    verification_error: str | None = None
    verified: bool = False




router = APIRouter(
    prefix="/v1/snapshot-control/vector-indexes",
    tags=["External Vector Index Registry"],
)




@lru_cache(maxsize=1)
def _registry() -> PostgresVectorIndexRegistry:
    return PostgresVectorIndexRegistry(settings)




def _error(exc: Exception) -> HTTPException:
    text = str(exc)
    if "not found" in text.lower():
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=text)
    if "different repository" in text.lower():
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=text)
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=text)




@router.post(
    "",
    response_model=VectorIndexRegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_vector_index(payload: VectorIndexCreate) -> VectorIndexRegisterResponse:
    try:
        record, deduplicated = _registry().register(payload)
        return VectorIndexRegisterResponse(
            vector_index=record,
            deduplicated=deduplicated,
        )
    except VectorIndexRegistryError as exc:
        raise _error(exc) from exc




@router.get("/{vector_index_id}", response_model=VectorIndexRecord)
def get_vector_index(vector_index_id: str) -> VectorIndexRecord:
    try:
        record = _registry().get(vector_index_id)
    except VectorIndexRegistryError as exc:
        raise _error(exc) from exc
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"vector index not found: {vector_index_id}",
        )
    return record




@router.get(
    "/by-snapshot/{snapshot_id}",
    response_model=list[VectorIndexRecord],
)
def list_snapshot_vector_indexes(snapshot_id: str) -> list[VectorIndexRecord]:
    try:
        return _registry().list_for_snapshot(snapshot_id)
    except VectorIndexRegistryError as exc:
        raise _error(exc) from exc




@router.get(
    "/{vector_index_id}/freshness",
    response_model=VectorIndexFreshness,
)
def get_vector_index_freshness(
    vector_index_id: str,
    snapshot_id: str = Query(..., min_length=1),
) -> VectorIndexFreshness:
    try:
        return _registry().freshness(snapshot_id, vector_index_id)
    except VectorIndexRegistryError as exc:
        raise _error(exc) from exc




@router.patch("/{vector_index_id}/status", response_model=VectorIndexRecord)
def update_vector_index_status(
    vector_index_id: str,
    payload: VectorIndexStatusUpdate,
) -> VectorIndexRecord:
    try:
        return _registry().update_status(
            vector_index_id,
            payload.status,
            verification_error=payload.verification_error,
            verified=payload.verified,
        )
    except VectorIndexRegistryError as exc:
        raise _error(exc) from exc