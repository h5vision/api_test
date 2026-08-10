from __future__ import annotations

import os
import socket
import sys

from .config import settings
from .distributed import DistributedStateError, RedisCoordinator


def main() -> int:
    consumer = os.getenv("WORKER_ID") or os.getenv("POD_NAME") or socket.gethostname()
    coordinator = RedisCoordinator(settings)
    try:
        if not coordinator.ping():
            return 1
        state = coordinator.worker_state(consumer)
    except DistributedStateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not state:
        print("worker heartbeat not found", file=sys.stderr)
        return 1
    status = str(state.get("status") or "")
    if status not in {"idle", "busy", "draining"}:
        print(f"invalid worker state: {status}", file=sys.stderr)
        return 1
    if status == "draining":
        print(status)
        return 1
    print(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

