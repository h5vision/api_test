"""Compatibility alias; implementation moved to ``backend.domains.repositories.repository_indexer``."""
import sys
from .domains.repositories import repository_indexer as _implementation
sys.modules[__name__] = _implementation
