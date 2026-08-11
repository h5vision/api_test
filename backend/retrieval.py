"""Compatibility alias for the canonical retrieval policy implementation."""
import sys
from .domains.retrieval import reranking as _implementation
sys.modules[__name__] = _implementation
