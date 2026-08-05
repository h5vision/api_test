from __future__ import annotations


# Backward-compatible alias for validation commands distributed before the
# Snapshot mount was integrated into the production ASGI entry point.
from .asgi import app


__all__ = ["app"]
