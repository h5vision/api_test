from __future__ import annotations

from pathlib import Path

from fastapi.routing import APIRoute

from backend.api.v1.chat import create_chat_router
from backend.schemas import ChatContextResponse, ChatResponse


ROOT = Path(__file__).resolve().parents[1]


def _handler(*args, **kwargs):
    return None


def test_chat_router_preserves_public_chat_route_contracts() -> None:
    router = create_chat_router(
        error_responses={},
        canonical_context_contract_handler=_handler,
        register_chat_context_handler=_handler,
        get_chat_context_handler=_handler,
        chat_stream_contract_handler=_handler,
        get_chat_citation_handler=_handler,
        chat_handler=_handler,
    )

    routes = {
        (next(iter(route.methods)), route.path): route
        for route in router.routes
        if isinstance(route, APIRoute)
    }
    assert set(routes) == {
        ("GET", "/v1/contracts/canonical-context"),
        ("POST", "/v1/chat/contexts"),
        ("GET", "/v1/chat/contexts/{context_id}"),
        ("GET", "/v1/contracts/chat-stream"),
        ("GET", "/v1/citations/{request_id}/{citation_id}"),
        ("POST", "/v1/chat"),
    }
    assert routes[("POST", "/v1/chat/contexts")].status_code == 201
    assert routes[("POST", "/v1/chat/contexts")].response_model is ChatContextResponse
    assert routes[("GET", "/v1/chat/contexts/{context_id}")].response_model is ChatContextResponse
    assert routes[("POST", "/v1/chat")].response_model is ChatResponse


def test_app_composes_chat_router_without_moving_chat_handlers() -> None:
    app_source = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")
    router_source = (ROOT / "backend" / "api" / "v1" / "chat.py").read_text(encoding="utf-8")

    assert "from .api.v1.chat import create_chat_router" in app_source
    assert "canonical_context_contract_handler=_legacy_app.canonical_context_contract" in app_source
    assert "register_chat_context_handler=_legacy_app.register_chat_context" in app_source
    assert "get_chat_context_handler=_legacy_app.get_chat_context" in app_source
    assert "chat_stream_contract_handler=_legacy_app.chat_stream_contract" in app_source
    assert "get_chat_citation_handler=_legacy_app.get_chat_citation" in app_source
    assert "chat_handler=_legacy_app.chat" in app_source
    assert "legacy_app" not in router_source
