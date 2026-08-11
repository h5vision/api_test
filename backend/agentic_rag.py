"""Compatibility alias for the canonical agentic RAG implementation."""
import sys
from .domains.rag import agentic as _implementation
sys.modules[__name__] = _implementation
