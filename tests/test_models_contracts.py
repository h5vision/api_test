from __future__ import annotations

from backend.contracts.models import (
    ModelAccessUpdateRequest,
    ModelAccessUpdateResponse,
    ModelInfo,
    ModelListResponse,
    OllamaScanResponse,
    OllamaScanTarget,
)
from backend import schemas


def test_models_contract_path_preserves_legacy_model_identity() -> None:
    assert ModelInfo is schemas.ModelInfo
    assert ModelListResponse is schemas.ModelListResponse
    assert ModelAccessUpdateRequest is schemas.ModelAccessUpdateRequest
    assert ModelAccessUpdateResponse is schemas.ModelAccessUpdateResponse
    assert OllamaScanTarget is schemas.OllamaScanTarget
    assert OllamaScanResponse is schemas.OllamaScanResponse
