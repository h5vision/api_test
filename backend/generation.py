"""Compatibility alias; generation ownership moved to ``backend.domains.generation.routing``."""
import sys
from .domains.generation import routing as _implementation
sys.modules[__name__] = _implementation
