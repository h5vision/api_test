"""Compatibility alias for the canonical Chat streaming implementation."""
from __future__ import annotations

import sys

from .domains.chat import streaming as _implementation

sys.modules[__name__] = _implementation
