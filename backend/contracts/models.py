"""Stable Models contract import path during the incremental refactor.

The canonical classes remain owned by ``backend.schemas`` for now so existing
imports and Pydantic model identity stay unchanged.  Phase 13 will invert this
compatibility layer after all callers have migrated to ``backend.contracts``.
"""

from ..schemas import (
    ModelAccessUpdateRequest,
    ModelAccessUpdateResponse,
    ModelInfo,
    ModelListResponse,
    OllamaScanResponse,
    OllamaScanTarget,
)

__all__ = [
    "ModelAccessUpdateRequest",
    "ModelAccessUpdateResponse",
    "ModelInfo",
    "ModelListResponse",
    "OllamaScanResponse",
    "OllamaScanTarget",
]
