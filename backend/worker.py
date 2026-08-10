from __future__ import annotations

import logging
import os
import signal
import socket
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from threading import Event

from .distributed import DistributedStateError, QueuedTask


logger = logging.getLogger("vision.worker")
stop_event = Event()


def _stop(_signum: int, _frame: object) -> None:
    # Kubernetes sends SIGTERM before terminationGracePeriodSeconds expires.
    # Stop accepting new work but allow the current task to finish and keep its
    # Redis lease alive so another replica does not reclaim it prematurely.
    stop_event.set()


def _dispatch(task: QueuedTask) -> None:
    # Import after process startup so API and worker use the same initialized
    # PostgreSQL, vector, embedding and upload contracts.
    from . import app as runtime

    if task.kind == "system.noop":
        delay_seconds = max(0, min(60, int(task.payload.get("delay_seconds") or 0)))
        if delay_seconds:
            stop_event.wait(delay_seconds)
        return
    if task.kind == "repository.index":
        job_id = str(task.payload["job_id"])
        job = runtime.repository_store.get_job(job_id)
        if job is None or job["status"] == "completed":
            return
        runtime.repository_indexer_for_current_runtime().run(job_id)
        return
    if task.kind == "repository.resume":
        job_id = str(task.payload["job_id"])
        job = runtime.repository_store.get_job(job_id)
        if job is None or job["status"] == "completed":
            return
        runtime.repository_indexer_for_current_runtime().resume(job_id)
        return
    if task.kind == "offline.import":
        job_id = str(task.payload["job_id"])
        job = runtime.repository_store.get_job(job_id)
        if job is None or job["status"] == "completed":
            return
        runtime.offline_embedding_importer_for_current_runtime().run(
            job_id,
            str(task.payload["artifact_id"]),
        )
        return
    if task.kind == "upload.process":
        upload_id = str(task.payload["upload_id"])
        if runtime.upload_manager.get(upload_id).status == "completed":
            return
        runtime._process_uploaded_repository(upload_id)
        return
    raise ValueError(f"Unsupported task kind: {task.kind}")


def _heartbeat(coordinator, consumer: str, status: str, task: QueuedTask | None, ttl: int) -> None:
    try:
        coordinator.worker_heartbeat(
            consumer,
            status=status,
            task=task,
            ttl_seconds=ttl,
        )
    except DistributedStateError:
        logger.exception("worker heartbeat update failed consumer=%s", consumer)


def main() -> None:
    from .app import redis_coordinator

    logging.basicConfig(
        level=os.getenv("WORKER_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    consumer = (
        os.getenv("WORKER_ID")
        or os.getenv("POD_NAME")
        or socket.gethostname()
    )
    heartbeat_ttl = max(15, int(os.getenv("WORKER_HEARTBEAT_TTL_SECONDS", "45")))
    lease_interval = max(5, int(os.getenv("WORKER_LEASE_RENEW_SECONDS", "30")))
    poll_block_ms = max(500, min(10_000, int(os.getenv("WORKER_POLL_BLOCK_MS", "5000"))))

    redis_coordinator.ensure_group()
    _heartbeat(redis_coordinator, consumer, "idle", None, heartbeat_ttl)
    logger.info("worker started consumer=%s", consumer)

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            while not stop_event.is_set():
                _heartbeat(redis_coordinator, consumer, "idle", None, heartbeat_ttl)
                try:
                    task = redis_coordinator.reclaim(consumer)
                    if task is None:
                        task = redis_coordinator.read(consumer, block_ms=poll_block_ms)
                except DistributedStateError:
                    logger.exception("Redis task read failed")
                    stop_event.wait(2)
                    continue
                if task is None:
                    continue

                logger.info("task started id=%s kind=%s", task.message_id, task.kind)
                _heartbeat(redis_coordinator, consumer, "busy", task, heartbeat_ttl)
                future = executor.submit(_dispatch, task)
                while True:
                    try:
                        future.result(timeout=lease_interval)
                        break
                    except FutureTimeout:
                        try:
                            redis_coordinator.renew(task, consumer)
                        except DistributedStateError:
                            logger.exception(
                                "task lease renewal failed id=%s",
                                task.message_id,
                            )
                        _heartbeat(
                            redis_coordinator,
                            consumer,
                            "draining" if stop_event.is_set() else "busy",
                            task,
                            heartbeat_ttl,
                        )

                try:
                    future.result()
                except Exception as exc:
                    logger.exception("task failed id=%s kind=%s", task.message_id, task.kind)
                    try:
                        redis_coordinator.fail(task, str(exc))
                    except DistributedStateError:
                        logger.exception("failed task could not be recorded")
                else:
                    try:
                        redis_coordinator.ack(task)
                    except DistributedStateError:
                        logger.exception("task acknowledgement failed")
                    else:
                        logger.info("task completed id=%s kind=%s", task.message_id, task.kind)

                if stop_event.is_set():
                    _heartbeat(redis_coordinator, consumer, "draining", None, heartbeat_ttl)
                    break
    finally:
        try:
            redis_coordinator.worker_forget(consumer)
        except DistributedStateError:
            logger.exception("worker heartbeat cleanup failed consumer=%s", consumer)
        logger.info("worker stopped consumer=%s", consumer)


if __name__ == "__main__":
    main()
