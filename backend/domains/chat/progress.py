from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from .schemas import API_SCHEMA_VERSION


ChatStreamEvent = Literal["meta", "status", "delta", "done", "error"]
ChatProgressStage = Literal[
    "sending",
    "reasoning",
    "thinking",
    "answering",
    "failed",
]

_EVENT_PROGRESS: dict[ChatStreamEvent, tuple[ChatProgressStage, str]] = {
    "meta": ("sending", "전송중"),
    "status": ("reasoning", "추론중"),
    "delta": ("thinking", "생각중"),
    "done": ("answering", "답변중"),
    "error": ("failed", "답변 실패"),
}


def simulated_chat_progress(
    request_id: str,
    event: ChatStreamEvent,
) -> dict[str, Any]:
    """Build the temporary per-event progress contract for chat SSE."""

    stage, label = _EVENT_PROGRESS[event]

    return {
        "schema_version": API_SCHEMA_VERSION,
        "request_id": request_id,
        "stage": stage,
        "label": label,
        "simulated": True,
        "progress_source": "vision-generator",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }
