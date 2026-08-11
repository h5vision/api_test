"""Compatibility alias for the canonical Chat intake implementation."""
from __future__ import annotations

import sys

from .domains.chat import intake as _implementation

sys.modules[__name__] = _implementation
