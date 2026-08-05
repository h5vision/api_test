from __future__ import annotations

import os

from granian.utils.proxies import wrap_asgi_with_proxy_headers

from .app import app as fastapi_app
from .snapshot_control_plane import mount_snapshot_control_plane


def _trusted_proxy_hosts() -> list[str]:
    raw = os.getenv("TRUSTED_PROXY_HOSTS", "172.30.0.0/24")
    return [host.strip() for host in raw.split(",") if host.strip()]


# Snapshot API is mounted on the production FastAPI app only when the feature
# flag is enabled. The mount helper is idempotent, so imports/reloads cannot
# duplicate the routes.
mount_snapshot_control_plane(fastapi_app)


# Traefik 네트워크에서 전달된 Forwarded 헤더만 신뢰한다.
app = wrap_asgi_with_proxy_headers(
    fastapi_app,
    trusted_hosts=_trusted_proxy_hosts(),
)
