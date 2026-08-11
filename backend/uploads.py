"""Compatibility alias; implementation moved to ``backend.domains.repositories.uploads``."""
import sys
from .domains.repositories import uploads as _implementation
sys.modules[__name__] = _implementation
