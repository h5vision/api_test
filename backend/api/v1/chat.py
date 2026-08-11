from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, status

from ...schemas import ChatContextResponse, ChatResponse


def create_chat_router(
    *,
    error_responses: dict[int | str, dict[str, Any]],
    canonical_context_contract_handler: Callable[..., Any],
    register_chat_context_handler: Callable[..., Any],
    get_chat_context_handler: Callable[..., Any],
    chat_stream_contract_handler: Callable[..., Any],
    get_chat_citation_handler: Callable[..., Any],
    chat_handler: Callable[..., Any],
) -> APIRouter:
    """Own the public Chat route surface without changing legacy handlers."""
    router = APIRouter()

    router.add_api_route(
        "/v1/contracts/canonical-context",
        canonical_context_contract_handler,
        methods=["GET"],
        tags=["Chat"],
        summary="Read the frozen P3 Canonical Context JSON Schema",
    )
    router.add_api_route(
        "/v1/chat/contexts",
        register_chat_context_handler,
        methods=["POST"],
        response_model=ChatContextResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["Chat"],
        summary="Register optional project and Snapshot context separately from Chat",
    )
    router.add_api_route(
        "/v1/chat/contexts/{context_id}",
        get_chat_context_handler,
        methods=["GET"],
        response_model=ChatContextResponse,
        tags=["Chat"],
        summary="Read an unexpired Chat Context owned by the current Client",
    )
    router.add_api_route(
        "/v1/contracts/chat-stream",
        chat_stream_contract_handler,
        methods=["GET"],
        tags=["Chat"],
        summary="Read the P3-C Chat SSE event contract",
    )
    router.add_api_route(
        "/v1/citations/{request_id}/{citation_id}",
        get_chat_citation_handler,
        methods=["GET"],
        tags=["Chat"],
        summary="Read one short-lived citation from a completed Chat response",
    )
    router.add_api_route(
        "/v1/chat",
        chat_handler,
        methods=["POST"],
        response_model=ChatResponse,
        tags=["Chat"],
        summary="Run general or separately-contextualized Chat",
        description=(
            "The minimal body is role, model_id, content and stream. Project, Git Commit "
            "and Snapshot data may be registered independently through POST /v1/chat/contexts "
            "and selected with X-Vision-Context-ID. Without that header Chat remains unscoped. "
            "stream=false returns JSON; stream=true returns meta/status/delta/done/error SSE."
        ),
        responses=error_responses,
    )
    return router
