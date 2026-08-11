"""Compatibility alias for the canonical retrieval evaluation implementation."""
import sys
from .domains.retrieval import evaluation as _implementation
sys.modules[__name__] = _implementation
