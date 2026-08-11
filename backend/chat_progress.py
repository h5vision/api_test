"""Compatibility alias for the canonical Chat progress implementation."""
from __future__ import annotations

import sys

from .domains.chat import progress as _implementation

sys.modules[__name__] = _implementation
