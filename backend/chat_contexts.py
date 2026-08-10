from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import uuid4

from .distributed import DistributedStateError, RedisCoordinator


class ChatContextError(RuntimeError):
    def __init__(self, message: str, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


class SnapshotLookup(Protocol):
    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None: ...

    def find_snapshots(
        self,
        *,
        project_id: str | None = None,
        revision: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]: ...

    def get_current_snapshot_context(self, project_id: str) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class ChatContextRecord:
    context_id: str
    owner_client_id: str
    project_id: str | None
    commit_id: str | None
    snapshot_id: str | None
    resolution: str
    grounding_available: bool
    created_at: str
    expires_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ChatContextRecord":
        return cls(
            context_id=str(value["context_id"]),
            owner_client_id=str(value["owner_client_id"]),
            project_id=(str(value["project_id"]) if value.get("project_id") else None),
            commit_id=(str(value["commit_id"]) if value.get("commit_id") else None),
            snapshot_id=(str(value["snapshot_id"]) if value.get("snapshot_id") else None),
            resolution=str(value.get("resolution") or "unscoped"),
            grounding_available=bool(value.get("grounding_available")),
            created_at=str(value["created_at"]),
            expires_at=str(value["expires_at"]),
        )


class ChatContextService:
    """Resolve optional project revision hints and share them through Redis.

    This service never makes Chat itself dependent on Context registration.
    A request without X-Vision-Context-ID remains an ordinary unscoped Chat.
    """

    namespace = "chat-context"

    def __init__(
        self,
        coordinator: RedisCoordinator,
        snapshots: SnapshotLookup,
        *,
        ttl_seconds: int = 21_600,
    ) -> None:
        self._coordinator = coordinator
        self._snapshots = snapshots
        self._ttl_seconds = max(300, int(ttl_seconds))

    @staticmethod
    def _revision(value: Any) -> str | None:
        normalized = str(value or "").strip().lower()
        return normalized or None

    def register(
        self,
        *,
        owner_client_id: str,
        project_id: str | None,
        commit_id: str | None,
        snapshot_id: str | None,
    ) -> ChatContextRecord:
        resolved_project = str(project_id or "").strip() or None
        resolved_commit = self._revision(commit_id)
        resolved_snapshot = str(snapshot_id or "").strip() or None
        resolution = "unscoped"

        try:
            if resolved_snapshot:
                snapshot = self._snapshots.get_snapshot(resolved_snapshot)
                if snapshot is None:
                    raise ChatContextError("snapshot_id를 찾을 수 없습니다.", 404)
                snapshot_project = str(snapshot.get("project_id") or "").strip() or None
                snapshot_commit = self._revision(snapshot.get("revision"))
                if resolved_project and snapshot_project and resolved_project != snapshot_project:
                    raise ChatContextError(
                        "project_id와 snapshot_id의 프로젝트가 일치하지 않습니다.", 409
                    )
                if resolved_commit and snapshot_commit and resolved_commit != snapshot_commit:
                    raise ChatContextError(
                        "commit_id와 snapshot_id의 Revision이 일치하지 않습니다.", 409
                    )
                resolved_project = resolved_project or snapshot_project
                resolved_commit = resolved_commit or snapshot_commit
                resolution = "snapshot_id"
            elif resolved_commit:
                matches = self._snapshots.find_snapshots(
                    project_id=resolved_project,
                    revision=resolved_commit,
                    limit=3,
                )
                if len(matches) > 1 and not resolved_project:
                    raise ChatContextError(
                        "commit_id가 여러 Snapshot과 일치합니다. project_id를 함께 보내세요.",
                        409,
                    )
                if matches:
                    match = matches[0]
                    if resolved_project and len(matches) > 1:
                        current = self._snapshots.get_current_snapshot_context(
                            resolved_project
                        )
                        current_snapshot_id = (
                            str(current.get("snapshot_id") or "").strip()
                            if current is not None
                            else ""
                        )
                        match = next(
                            (
                                row
                                for row in matches
                                if str(row.get("snapshot_id") or "").strip()
                                == current_snapshot_id
                            ),
                            matches[0],
                        )
                    resolved_project = str(match.get("project_id") or "").strip() or resolved_project
                    resolved_snapshot = str(match.get("snapshot_id") or "").strip() or None
                    resolution = "project_commit" if project_id else "unique_commit"
                else:
                    resolution = "commit_unresolved"
            elif resolved_project:
                current = self._snapshots.get_current_snapshot_context(resolved_project)
                if current is not None:
                    resolved_snapshot = str(current.get("snapshot_id") or "").strip() or None
                    resolved_commit = self._revision(current.get("revision"))
                    resolution = "project_current_snapshot"
                else:
                    resolution = "project_only"
        except ChatContextError:
            raise
        except Exception as exc:
            raise ChatContextError("Chat Context Snapshot 조회에 실패했습니다.") from exc

        now = datetime.now(timezone.utc)
        record = ChatContextRecord(
            context_id=f"ctx_{uuid4().hex}",
            owner_client_id=owner_client_id,
            project_id=resolved_project,
            commit_id=resolved_commit,
            snapshot_id=resolved_snapshot,
            resolution=resolution,
            grounding_available=bool(resolved_project and resolved_snapshot),
            created_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=self._ttl_seconds)).isoformat(),
        )
        try:
            self._coordinator.set_ephemeral_json(
                self.namespace,
                record.context_id,
                record.to_dict(),
                ttl_seconds=self._ttl_seconds,
            )
        except DistributedStateError as exc:
            raise ChatContextError("Chat Context 공유 저장소를 사용할 수 없습니다.") from exc
        return record

    def get(self, context_id: str, *, owner_client_id: str) -> ChatContextRecord:
        try:
            value = self._coordinator.get_ephemeral_json(self.namespace, context_id)
        except DistributedStateError as exc:
            raise ChatContextError("Chat Context 공유 저장소를 사용할 수 없습니다.") from exc
        if value is None:
            raise ChatContextError("Chat Context가 없거나 만료되었습니다.", 404)
        record = ChatContextRecord.from_dict(value)
        if record.owner_client_id != owner_client_id:
            raise ChatContextError("다른 Frontend Client의 Chat Context입니다.", 403)
        return record
