from __future__ import annotations


# Compatibility shim for the earlier experimental entry point. The real
# implementation keeps trusted proxy handling and feature-flag mounting in one place.
from .asgi_snapshot_mvp import app


__all__ = ["app"]