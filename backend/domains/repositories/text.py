from ...text import *  # noqa: F401,F403
from ...text import __dict__ as _implementation_namespace

def __getattr__(name: str):
    try: return _implementation_namespace[name]
    except KeyError as exc: raise AttributeError(name) from exc
