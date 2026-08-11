"""Compatibility alias; implementation moved to ``backend.domains.repositories.offline_embeddings``."""
import sys
from .domains.repositories import offline_embeddings as _implementation
sys.modules[__name__] = _implementation
