"""Compatibility alias; implementation moved to ``backend.domains.repositories.repository_store``."""
import sys
from .domains.repositories import repository_store as _implementation
sys.modules[__name__] = _implementation
