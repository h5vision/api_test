"""Compatibility import path; implementation moved to ``backend.domains.vector_indexes.vector_gateway``."""

from .domains.vector_indexes.vector_gateway import *  # noqa: F401,F403
from .domains.vector_indexes.vector_gateway import __dict__ as _implementation_namespace

def __getattr__(name: str):
    try:
        return _implementation_namespace[name]
    except KeyError as exc:
        raise AttributeError(name) from exc

def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_implementation_namespace))
