from __future__ import annotations

from pathlib import Path

import pytest

from backend.chat_contexts import ChatContextError, ChatContextService
from backend.chat_routing import classify_chat_request
from backend.chat_streaming import wants_chat_sse
from backend.schemas import ChatContextRegistrationRequest, ChatRequest


ROOT = Path(__file__).resolve().parents[1]
COMMIT = "a" * 40


class _Coordinator:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], dict] = {}

    def set_ephemeral_json(self, namespace, key, value, *, ttl_seconds):
        assert ttl_seconds >= 300
        self.values[(namespace, key)] = value

    def get_ephemeral_json(self, namespace, key):
        return self.values.get((namespace, key))


class _Snapshots:
    def __init__(self) -> None:
        self.rows = [
            {
                "snapshot_id": "snap_1",
                "project_id": "h5vision/fest-api",
                "revision": COMMIT,
            }
        ]

    def get_snapshot(self, snapshot_id):
        return next((row for row in self.rows if row["snapshot_id"] == snapshot_id), None)

    def find_snapshots(self, *, project_id=None, revision=None, limit=20):
        return [
            row
            for row in self.rows
            if (not project_id or row["project_id"] == project_id)
            and (not revision or row["revision"] == revision)
        ][:limit]

    def get_current_snapshot_context(self, project_id):
        return next((row for row in self.rows if row["project_id"] == project_id), None)


def _service():
    return ChatContextService(_Coordinator(), _Snapshots(), ttl_seconds=600)


def test_minimal_chat_contract_needs_no_project_or_snapshot() -> None:
    payload = ChatRequest.model_validate(
        {
            "role": "user",
            "model_id": "backendai:model-a",
            "content": "일반적인 질문입니다.",
            "stream": True,
        }
    )

    assert payload.message == "일반적인 질문입니다."
    assert payload.project_id == "__auto__"
    assert payload.snapshot_id is None
    assert payload.stream is True
    assert classify_chat_request(payload).route == "general"


def test_project_words_without_separate_identity_remain_general_chat() -> None:
    payload = ChatRequest.model_validate(
        {"role": "user", "content": "이 프로젝트와 app.py를 설명해줘"}
    )

    decision = classify_chat_request(payload)

    assert decision.project_required is False
    assert "project_language_without_identity" in decision.reasons


def test_separate_context_fields_are_all_optional() -> None:
    payload = ChatContextRegistrationRequest.model_validate({})

    assert payload.project_id is None
    assert payload.commit_id is None
    assert payload.snapshot_id is None


def test_accept_header_requests_sse_without_stream_body_field() -> None:
    payload = ChatRequest.model_validate(
        {"role": "user", "content": "SSE 요청"}
    )

    assert "stream" not in payload.intake_input_fields
    assert wants_chat_sse(
        stream=payload.stream,
        input_fields=payload.intake_input_fields,
        accept_header="text/event-stream",
    ) is True


def test_explicit_stream_false_overrides_sse_accept_header() -> None:
    payload = ChatRequest.model_validate(
        {"role": "user", "content": "JSON 요청", "stream": False}
    )

    assert wants_chat_sse(
        stream=payload.stream,
        input_fields=payload.intake_input_fields,
        accept_header="text/event-stream",
    ) is False


def test_context_resolves_project_commit_to_snapshot_and_is_owner_scoped() -> None:
    service = _service()

    record = service.register(
        owner_client_id="client-a",
        project_id="h5vision/fest-api",
        commit_id=COMMIT.upper(),
        snapshot_id=None,
    )

    assert record.commit_id == COMMIT
    assert record.snapshot_id == "snap_1"
    assert record.grounding_available is True
    assert service.get(record.context_id, owner_client_id="client-a") == record
    with pytest.raises(ChatContextError, match="다른 Frontend Client"):
        service.get(record.context_id, owner_client_id="client-b")


def test_unresolved_commit_does_not_claim_grounding() -> None:
    service = _service()

    record = service.register(
        owner_client_id="client-a",
        project_id="h5vision/fest-api",
        commit_id="b" * 40,
        snapshot_id=None,
    )

    assert record.resolution == "commit_unresolved"
    assert record.grounding_available is False


def test_snapshot_commit_mismatch_is_rejected_in_context_endpoint_only() -> None:
    service = _service()

    with pytest.raises(ChatContextError, match="Revision") as captured:
        service.register(
            owner_client_id="client-a",
            project_id="h5vision/fest-api",
            commit_id="b" * 40,
            snapshot_id="snap_1",
        )

    assert captured.value.status_code == 409


def test_public_chat_source_contains_sse_and_context_contracts() -> None:
    source = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")

    assert '"/v1/chat/contexts"' in source
    assert '"/v1/contracts/chat-stream"' in source
    assert '"/v1/citations/{request_id}/{citation_id}"' in source
    assert 'media_type="text/event-stream"' in source
    assert '"Content-Type": "text/event-stream"' in source
    assert '"X-Vision-Chat-Transport": "sse"' in source
    assert "stream=true is not supported yet" not in source
    assert "if prompt_result.has_evidence\n        else []" in source
