from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import patch

from backend.config import settings
from backend.generation import GenerationRouter


class FakeResponse:
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def __iter__(self):
        return iter(self.lines)


def configured_router() -> GenerationRouter:
    configured = replace(
        settings,
        backendai_base_url="http://192.0.2.10:11500",
        backendai_model="qwen-test:7b",
        backendai_public_model_id="backendai-default",
    )
    router = GenerationRouter(configured)
    router.backendai_status = lambda force=False: {
        "connected": True,
        "models": ["qwen-test:7b"],
    }
    return router


def test_streaming_ai_transport_sends_revision_headers_and_structured_context():
    router = configured_router()
    response = FakeResponse(
        [
            b'{"message":{"content":"ok"},"done":false}\n',
            b'{"message":{"content":""},"done":true}\n',
        ]
    )
    captured = []

    def fake_urlopen(request, timeout):
        captured.append(request)
        return response

    vision_context = {
        "schema_version": "1.0",
        "project_id": "h5vision/vision",
        "snapshot_id": "snap_123",
        "revision": {
            "snapshot_sha": "a" * 40,
            "local_sha": "b" * 40,
        },
        "diff": {"status": "available", "files": []},
    }
    with patch(
        "backend.integrations.ai_server.ollama.urllib.request.urlopen",
        side_effect=fake_urlopen,
    ):
        stream = router.stream_backendai(
            "backendai-default",
            [{"role": "user", "content": "question"}],
            request_id="req-stream",
            routing_metadata={
                "snapshot_id": "snap_123",
                "base_revision": "a" * 40,
                "target_revision": "b" * 40,
            },
            vision_context=vision_context,
        )
        assert "".join(stream.deltas) == "ok"

    assert len(captured) == 1
    request = captured[0]
    body = json.loads(request.data.decode("utf-8"))
    assert body["vision_context"] == vision_context
    headers = {key.lower(): value for key, value in request.header_items()}
    assert headers["x-vision-snapshot-id"] == "snap_123"
    assert headers["x-vision-base-revision"] == "a" * 40
    assert headers["x-vision-target-revision"] == "b" * 40


def test_nonstream_ai_transport_keeps_same_revision_metadata_and_body():
    router = configured_router()
    observed = {}

    def fake_chat(base_url, payload, api_key, timeout_seconds, *, extra_headers=None):
        observed["payload"] = payload
        observed["headers"] = extra_headers or {}
        return {"message": {"content": "ok"}}

    vision_context = {
        "schema_version": "1.0",
        "snapshot_id": "snap_123",
        "diff": {"status": "not_needed", "files": []},
    }
    with patch(
        "backend.domains.generation.orchestration_backendai.ollama_chat",
        side_effect=fake_chat,
    ):
        result = router.generate(
            "backendai-default",
            "question",
            [],
            [],
            "",
            "h5vision/vision",
            "session",
            request_id="req-json",
            routing_metadata={"snapshot_id": "snap_123"},
            vision_context=vision_context,
        )

    assert result.answer == "ok"
    assert observed["payload"]["vision_context"] == vision_context
    assert observed["headers"]["X-Vision-Snapshot-Id"] == "snap_123"


def test_generate_delta_callback_preserves_snapshot_transport_end_to_end():
    router = configured_router()
    observed = {}
    deltas = []
    vision_context = {
        "schema_version": "1.0",
        "project_id": "h5vision/vision",
        "snapshot_id": "snap_123",
        "revision": {
            "snapshot_sha": "a" * 40,
            "local_sha": "b" * 40,
            "remote_resolution_reason": "branch_unproven",
        },
        "diff": {"status": "available", "files": []},
    }

    def fake_stream_chat(
        base_url,
        payload,
        api_key,
        timeout_seconds,
        *,
        extra_headers=None,
    ):
        observed["payload"] = payload
        observed["headers"] = extra_headers or {}
        yield "hel"
        yield "lo"

    with patch(
        "backend.domains.generation.execution_backendai.stream_ollama_chat",
        side_effect=fake_stream_chat,
    ):
        result = router.generate(
            "backendai-default",
            "question",
            [],
            [],
            "",
            "h5vision/vision",
            "session",
            request_id="req-generate-stream",
            delta_callback=deltas.append,
            routing_metadata={
                "snapshot_id": "snap_123",
                "base_revision": "a" * 40,
                "target_revision": "b" * 40,
            },
            vision_context=vision_context,
        )

    assert result.answer == "hello"
    assert deltas == ["hel", "lo"]
    assert observed["payload"]["stream"] is True
    assert observed["payload"]["vision_context"] == vision_context
    assert observed["headers"]["X-Vision-Snapshot-Id"] == "snap_123"
    assert observed["headers"]["X-Vision-Base-Revision"] == "a" * 40
    assert observed["headers"]["X-Vision-Target-Revision"] == "b" * 40
