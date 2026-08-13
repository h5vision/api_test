from __future__ import annotations

import asyncio
import json

from fastapi import HTTPException
from starlette.requests import Request

from backend import legacy_app
from backend.schemas import ChatRequest


def _request() -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("192.0.2.10", 50000),
            "scheme": "http",
        }
    )
    request.state.request_id = "req_sse_error_contract"
    return request


def _error_event_payload(events: list[str]) -> dict[str, object]:
    error_event = next(event for event in events if "event: error\n" in event)
    data_line = next(
        line for line in error_event.splitlines() if line.startswith("data: ")
    )
    return json.loads(data_line.removeprefix("data: "))


def test_sse_http_error_exposes_message_and_legacy_error_alias(monkeypatch) -> None:
    detail = "BackendAI 서버에서 사용할 수 없는 model_id입니다."

    def fail_chat(*_args, **_kwargs):
        raise HTTPException(status_code=422, detail=detail)

    monkeypatch.setattr(legacy_app, "_chat_json", fail_chat)
    payload = ChatRequest.model_validate(
        {"role": "user", "content": "모델 확인", "stream": True}
    )

    async def collect() -> list[str]:
        return [event async for event in legacy_app._chat_sse_body(payload, _request())]

    body = _error_event_payload(asyncio.run(collect()))

    assert body["message"] == detail
    assert body["error"] == detail
    assert body["code"] == "VALIDATION_ERROR"
    assert body["status_code"] == 422
    assert body["retryable"] is False


def test_sse_internal_error_exposes_message_and_legacy_error_alias(monkeypatch) -> None:
    detail = "unexpected provider failure"

    def fail_chat(*_args, **_kwargs):
        raise RuntimeError(detail)

    monkeypatch.setattr(legacy_app, "_chat_json", fail_chat)
    payload = ChatRequest.model_validate(
        {"role": "user", "content": "오류 확인", "stream": True}
    )

    async def collect() -> list[str]:
        return [event async for event in legacy_app._chat_sse_body(payload, _request())]

    body = _error_event_payload(asyncio.run(collect()))

    assert body["message"] == detail
    assert body["error"] == detail
    assert body["code"] == "INTERNAL_ERROR"
    assert body["status_code"] == 500
    assert body["retryable"] is False
