"""Compatibility alias for the VectorDB rag_lab adapter."""
import sys
from .integrations.vectordb import rag_lab as _implementation
sys.modules[__name__] = _implementation
