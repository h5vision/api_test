from __future__ import annotations


import os


from fastapi import FastAPI




_TRUE_VALUES = {"1", "true", "yes", "on"}




def snapshot_control_plane_enabled() -> bool:
    return (
        os.getenv("SNAPSHOT_CONTROL_PLANE_ENABLED", "false").strip().lower()
        in _TRUE_VALUES
    )




def mount_snapshot_control_plane(app: FastAPI) -> bool:
    """Conditionally mount the public GitHub Commit Snapshot MVP once.


    The production ASGI entry point remains backend.asgi:app. This function is
    intentionally safe to call more than once and does not inspect route.path.
    """


    if not snapshot_control_plane_enabled():
        return False
    if getattr(app.state, "snapshot_control_plane_mounted", False):
        return False


    from .snapshots.router import router


    app.include_router(router)
    app.state.snapshot_control_plane_mounted = True
    return True