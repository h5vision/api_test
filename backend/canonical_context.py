"""Compatibility alias for the canonical Chat context contract implementation."""
from __future__ import annotations

import sys

from .domains.chat import canonical_context as _implementation

sys.modules[__name__] = _implementation
