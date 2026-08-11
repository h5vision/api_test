from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

from fastapi import APIRouter, Response

from ...model_catalog import model_catalog_revision
from ...contracts.models import ModelListResponse


class GenerationCatalog(Protocol):
    @property
    def default_model_id(self) -> str:
        ...

    def models(self) -> list[Any]:
        ...


def create_models_router(generation_router: GenerationCatalog) -> APIRouter:
    """Build the public model-catalog router around the configured generation runtime.

    The factory keeps runtime wiring in ``backend.app`` while route ownership lives
    in the API layer.  The public path and response contract intentionally remain
    identical to the legacy inline route during the incremental refactor.
    """

    router = APIRouter()

    @router.get(
        "/v1/models",
        response_model=ModelListResponse,
        tags=["System"],
        summary="List selectable generation models",
    )
    def list_models(response: Response) -> ModelListResponse:
        response.headers["Cache-Control"] = "no-store"
        models = [model for model in generation_router.models() if model.enabled]
        default_model_id = generation_router.default_model_id
        return ModelListResponse(
            catalog_revision=model_catalog_revision(default_model_id, models),
            default_model_id=default_model_id,
            checked_at=datetime.now(timezone.utc),
            models=models,
        )

    return router
