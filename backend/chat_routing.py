"""Compatibility alias for the canonical Chat routing implementation."""
from __future__ import annotations

import sys

from .domains.chat import routing as _implementation

sys.modules[__name__] = _implementation
