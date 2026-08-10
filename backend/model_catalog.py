from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from .schemas import ModelInfo


def model_catalog_revision(
    default_model_id: str,
    models: Sequence[ModelInfo],
) -> str:
    """Content-derived revision for the selectable model catalog.

    Volatile runtime availability/endpoint data is intentionally excluded so
    temporary provider health flaps do not churn the Frontend catalog revision.
    """
    canonical = {
        "default_model_id": default_model_id,
        "models": [
            {
                "model_id": item.model_id,
                "model_name": item.model_name,
                "display_name": item.display_name,
                "provider": item.provider,
                "location": item.location,
                "deployment_type": item.deployment_type,
                "enabled": item.enabled,
                "is_default": item.is_default,
                "streaming": item.streaming,
            }
            for item in sorted(models, key=lambda value: value.model_id)
        ],
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"mc_{hashlib.sha256(encoded).hexdigest()[:24]}"

