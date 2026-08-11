"""Compatibility alias for the canonical Chat context implementation."""
from __future__ import annotations

import sys

from .domains.chat import contexts as _implementation

sys.modules[__name__] = _implementation
