from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from time import time
from typing import Any
from uuid import uuid4

from redis import Redis
from redis.exceptions import RedisError

from .config import Settings


class DistributedStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class QueuedTask:
    message_id: str
    kind: str
    payload: dict[str, Any]
    dedupe_key: str


class RedisCoordinator:
    """Shared metrics and reliable Redis Stream queue for all API replicas."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.stream = settings.task_queue_name
        self.group = f"{self.stream}:workers"
        self.processing_key = f"{self.stream}:processing"
        self.dedupe_prefix = f"{self.stream}:dedupe:"
        self.metrics_prefix = "vision:metrics"
        self.client = Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password or None,
            db=settings.redis_db,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=15,
            health_check_interval=30,
        )

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except RedisError as exc:
            raise DistributedStateError("Redis is unavailable") from exc

    def ensure_group(self) -> None:
        try:
            self.client.xgroup_create(
                self.stream,
                self.group,
                id="0",
                mkstream=True,
            )
        except RedisError as exc:
            if "BUSYGROUP" not in str(exc):
                raise DistributedStateError(
                    "Redis task consumer group initialization failed"
                ) from exc

    def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        dedupe_key: str,
        dedupe_ttl_seconds: int = 86_400,
    ) -> bool:
        normalized_dedupe = dedupe_key.strip()
        if not normalized_dedupe:
            raise ValueError("dedupe_key must not be blank")
        redis_key = f"{self.dedupe_prefix}{normalized_dedupe}"
        try:
            reserved = self.client.set(
                redis_key,
                "queued",
                nx=True,
                ex=dedupe_ttl_seconds,
            )
            if not reserved:
                return False
            try:
                self.client.xadd(
                    self.stream,
                    {
                        "kind": kind,
                        "payload": json.dumps(
                            payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "dedupe_key": normalized_dedupe,
                        "queued_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except Exception:
                self.client.delete(redis_key)
                raise
            return True
        except (RedisError, TypeError, ValueError) as exc:
            raise DistributedStateError("Task enqueue failed") from exc

    def read(
        self,
        consumer: str,
        *,
        block_ms: int = 5_000,
    ) -> QueuedTask | None:
        self.ensure_group()
        try:
            result = self.client.xreadgroup(
                self.group,
                consumer,
                {self.stream: ">"},
                count=1,
                block=block_ms,
            )
        except RedisError as exc:
            raise DistributedStateError("Task dequeue failed") from exc
        if not result:
            return None
        _stream, messages = result[0]
        message_id, values = messages[0]
        try:
            payload = json.loads(values.get("payload") or "{}")
        except json.JSONDecodeError as exc:
            self.ack(
                QueuedTask(
                    message_id=message_id,
                    kind=str(values.get("kind") or "invalid"),
                    payload={},
                    dedupe_key=str(values.get("dedupe_key") or ""),
                )
            )
            raise DistributedStateError("Queued task payload is invalid") from exc
        task = QueuedTask(
            message_id=message_id,
            kind=str(values.get("kind") or ""),
            payload=payload,
            dedupe_key=str(values.get("dedupe_key") or ""),
        )
        try:
            self.client.hset(
                self.processing_key,
                message_id,
                json.dumps(
                    {
                        "consumer": consumer,
                        "kind": task.kind,
                        "payload": task.payload,
                        "started_at": datetime.now(timezone.utc).isoformat(),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
        except RedisError:
            pass
        return task

    def reclaim(
        self,
        consumer: str,
        *,
        min_idle_ms: int = 120_000,
    ) -> QueuedTask | None:
        self.ensure_group()
        try:
            result = self.client.xautoclaim(
                self.stream,
                self.group,
                consumer,
                min_idle_ms,
                "0-0",
                count=1,
            )
        except RedisError as exc:
            raise DistributedStateError("Stalled task reclaim failed") from exc
        messages = result[1] if len(result) > 1 else []
        if not messages:
            return None
        message_id, values = messages[0]
        try:
            payload = json.loads(values.get("payload") or "{}")
        except json.JSONDecodeError as exc:
            raise DistributedStateError("Reclaimed task payload is invalid") from exc
        return QueuedTask(
            message_id=message_id,
            kind=str(values.get("kind") or ""),
            payload=payload,
            dedupe_key=str(values.get("dedupe_key") or ""),
        )

    def renew(self, task: QueuedTask, consumer: str) -> None:
        try:
            self.client.xclaim(
                self.stream,
                self.group,
                consumer,
                min_idle_time=0,
                message_ids=[task.message_id],
                justid=True,
            )
        except RedisError as exc:
            raise DistributedStateError("Task lease renewal failed") from exc

    def ack(self, task: QueuedTask) -> None:
        try:
            pipeline = self.client.pipeline(transaction=True)
            pipeline.xack(self.stream, self.group, task.message_id)
            pipeline.xdel(self.stream, task.message_id)
            pipeline.hdel(self.processing_key, task.message_id)
            if task.dedupe_key:
                pipeline.delete(f"{self.dedupe_prefix}{task.dedupe_key}")
            pipeline.execute()
        except RedisError as exc:
            raise DistributedStateError("Task acknowledgement failed") from exc

    def fail(self, task: QueuedTask, error: str) -> None:
        try:
            pipeline = self.client.pipeline(transaction=True)
            pipeline.xack(self.stream, self.group, task.message_id)
            pipeline.xdel(self.stream, task.message_id)
            pipeline.hdel(self.processing_key, task.message_id)
            pipeline.xadd(
                f"{self.stream}:dead",
                {
                    "kind": task.kind,
                    "payload": json.dumps(
                        task.payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    "dedupe_key": task.dedupe_key,
                    "error": error[:2_000],
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                },
                maxlen=1_000,
                approximate=True,
            )
            if task.dedupe_key:
                pipeline.delete(f"{self.dedupe_prefix}{task.dedupe_key}")
            pipeline.execute()
        except RedisError as exc:
            raise DistributedStateError("Failed task recording failed") from exc

    def request_started(self, path: str) -> None:
        minute = int(time() // 60)
        active_key = (
            f"{self.metrics_prefix}:active_requests:{self.settings.instance_id}"
        )
        try:
            pipeline = self.client.pipeline(transaction=False)
            pipeline.incr(active_key)
            pipeline.expire(active_key, 60)
            pipeline.incr(f"{self.metrics_prefix}:requests:{minute}")
            pipeline.expire(f"{self.metrics_prefix}:requests:{minute}", 180)
            pipeline.hincrby(f"{self.metrics_prefix}:paths:{minute}", path, 1)
            pipeline.expire(f"{self.metrics_prefix}:paths:{minute}", 180)
            pipeline.set(
                f"{self.metrics_prefix}:instances:{self.settings.instance_id}",
                datetime.now(timezone.utc).isoformat(),
                ex=60,
            )
            pipeline.execute()
        except RedisError as exc:
            raise DistributedStateError("Request metric update failed") from exc

    def request_finished(self) -> None:
        script = """
        local value = redis.call('DECR', KEYS[1])
        if value < 0 then
          redis.call('SET', KEYS[1], 0)
          return 0
        end
        return value
        """
        try:
            self.client.eval(
                script,
                1,
                (
                    f"{self.metrics_prefix}:active_requests:"
                    f"{self.settings.instance_id}"
                ),
            )
        except RedisError as exc:
            raise DistributedStateError("Request metric update failed") from exc

    def snapshot(self) -> dict[str, Any]:
        minute = int(time() // 60)
        try:
            active_keys = self.client.keys(
                f"{self.metrics_prefix}:active_requests:*"
            )
            active_values = (
                self.client.mget(active_keys) if active_keys else []
            )
            active = sum(int(value or 0) for value in active_values)
            requests_current = int(
                self.client.get(f"{self.metrics_prefix}:requests:{minute}") or 0
            )
            requests_previous = int(
                self.client.get(f"{self.metrics_prefix}:requests:{minute - 1}") or 0
            )
            stream_length = int(self.client.xlen(self.stream))
            groups = self.client.xinfo_groups(self.stream)
            worker_group = next(
                (
                    group
                    for group in groups
                    if str(group.get("name") or "") == self.group
                ),
                None,
            )
            processing = int(self.client.hlen(self.processing_key))
            if worker_group is not None and worker_group.get("lag") is not None:
                queue_depth = int(worker_group["lag"])
            else:
                # Older Redis versions may not expose group lag.
                queue_depth = max(0, stream_length - processing)
            instances = len(
                self.client.keys(f"{self.metrics_prefix}:instances:*")
            )
            dead = int(self.client.xlen(f"{self.stream}:dead"))
        except RedisError as exc:
            raise DistributedStateError("Runtime metric lookup failed") from exc
        return {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "active_requests": active,
            "requests_current_minute": requests_current,
            "requests_previous_minute": requests_previous,
            "queue_depth": queue_depth,
            "processing_tasks": processing,
            "dead_tasks": dead,
            "api_instances": instances,
        }


def new_task_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"
