from __future__ import annotations


from functools import lru_cache


from fastapi import APIRouter, HTTPException, status


from ..config import settings
from ..snapshots.repository import PostgresSnapshotRepository
from ..snapshots.resolver import SnapshotResolver
from .registry import (
    ExchangeCreate,
    ExchangeHub,
    ExchangeHubError,
    ExchangeRecord,
    ExchangeUpdate,
    PostgresExchangeStore,
)




router = APIRouter(
    prefix="/v1/snapshot-control/exchanges",
    tags=["Snapshot Exchange Hub"],
)




@lru_cache(maxsize=1)
def _hub() -> ExchangeHub:
    snapshot_repository = PostgresSnapshotRepository(settings)
    return ExchangeHub(
        SnapshotResolver(snapshot_repository),
        PostgresExchangeStore(settings),
    )




def _error(exc: ExchangeHubError) -> HTTPException:
    if "not found" in str(exc).lower():
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=str(exc),
    )




@router.post("", response_model=ExchangeRecord, status_code=status.HTTP_202_ACCEPTED)
def request_exchange(payload: ExchangeCreate) -> ExchangeRecord:
    try:
        return _hub().request(payload)
    except ExchangeHubError as exc:
        raise _error(exc) from exc




@router.get("/{exchange_id}", response_model=ExchangeRecord)
def get_exchange(exchange_id: str) -> ExchangeRecord:
    try:
        record = _hub().get(exchange_id)
    except ExchangeHubError as exc:
        raise _error(exc) from exc
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"exchange not found: {exchange_id}",
        )
    return record




@router.patch("/{exchange_id}", response_model=ExchangeRecord)
def update_exchange(exchange_id: str, payload: ExchangeUpdate) -> ExchangeRecord:
    try:
        return _hub().update(exchange_id, payload)
    except ExchangeHubError as exc:
        raise _error(exc) from exc