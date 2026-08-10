from __future__ import annotations

import json
from pathlib import Path

from backend.chat_intake import normalize_chat_intake, resolve_deep_normalization
from backend.schemas import ChatRequest


ROOT = Path(__file__).resolve().parents[1]


def test_separate_snapshot_contract_is_normalized() -> None:
    payload = ChatRequest.model_validate(
        {
            "project_id": "h5vision/fest-api",
            "snapshot": {
                "snapshotId": "snap_commit_123",
                "revision": "0123456789abcdef",
            },
            "message": "이 Snapshot의 변경점을 설명해줘",
        }
    )

    assert payload.project_id == "h5vision/fest-api"
    assert payload.snapshot_id == "snap_commit_123"
    assert payload.snapshot is not None
    assert payload.snapshot.revision == "0123456789abcdef"


def test_embedded_message_envelope_supplies_project_and_snapshot() -> None:
    outer = ChatRequest.model_validate(
        {
            "message": json.dumps(
                {
                    "projectId": "h5vision/fest-api",
                    "snapshotId": "snap_message_1",
                    "content": "이 프로젝트의 진입점을 찾아줘",
                    "history": [{"role": "user", "content": "구조부터 보자"}],
                },
                ensure_ascii=False,
            )
        }
    )

    result = normalize_chat_intake(outer, deep_enabled=True)

    assert result.input_format == "embedded_json"
    assert not result.fallback_used
    assert result.payload.project_id == "h5vision/fest-api"
    assert result.payload.snapshot_id == "snap_message_1"
    assert result.payload.message == "이 프로젝트의 진입점을 찾아줘"
    assert result.payload.history[0].content == "구조부터 보자"


def test_explicit_outer_snapshot_contract_wins_over_message_hints() -> None:
    outer = ChatRequest.model_validate(
        {
            "project_id": "outer/project",
            "snapshot_id": "snap_outer",
            "message": json.dumps(
                {
                    "project_id": "inner/project",
                    "snapshotId": "snap_inner",
                    "message": "충돌 우선순위를 확인해줘",
                },
                ensure_ascii=False,
            ),
        }
    )

    result = normalize_chat_intake(outer, deep_enabled=True)

    assert result.payload.project_id == "outer/project"
    assert result.payload.snapshot_id == "snap_outer"
    assert result.conflicts == (
        "project_id:outer_wins",
        "snapshot_id:outer_wins",
    )


def test_outer_auto_sentinel_does_not_hide_a_message_project_hint() -> None:
    outer = ChatRequest.model_validate(
        {
            "project_id": "default",
            "message": json.dumps(
                {
                    "project_id": "h5vision/fest-api",
                    "message": "프로젝트 구조를 보여줘",
                },
                ensure_ascii=False,
            ),
        }
    )

    result = normalize_chat_intake(outer, deep_enabled=True)

    assert result.payload.project_id == "h5vision/fest-api"
    assert result.conflicts == ()


def test_labeled_plain_message_can_supply_project_and_snapshot() -> None:
    outer = ChatRequest.model_validate(
        {
            "message": (
                "project_id=h5vision/fest-api; snapshotId=snap_inline_1; "
                "질문: 이 시점의 실행 구조를 설명해줘"
            )
        }
    )

    result = normalize_chat_intake(outer, deep_enabled=True)

    assert result.input_format == "labeled_text"
    assert result.payload.project_id == "h5vision/fest-api"
    assert result.payload.snapshot_id == "snap_inline_1"
    assert result.payload.message == "이 시점의 실행 구조를 설명해줘"


def test_deep_off_preserves_message_verbatim_while_basic_schema_stays_active() -> None:
    encoded = json.dumps(
        {"project_id": "inner/project", "message": "내부 질문"},
        ensure_ascii=False,
    )
    outer = ChatRequest.model_validate({"role": "user", "content": encoded})

    result = normalize_chat_intake(outer, deep_enabled=False)

    assert result.payload.message == encoded
    assert result.payload.role == "user"
    assert result.payload.project_id == "__auto__"


def test_invalid_or_unrecognized_json_falls_back_without_destroying_code() -> None:
    invalid = ChatRequest.model_validate({"message": '{"message": "broken"'})
    invalid_result = normalize_chat_intake(invalid, deep_enabled=True)
    assert invalid_result.payload.message == invalid.message
    assert invalid_result.fallback_used
    assert invalid_result.error_code == "embedded_json_invalid"

    code_json = ChatRequest.model_validate({"message": '{"function": "translate_page"}'})
    code_result = normalize_chat_intake(code_json, deep_enabled=True)
    assert code_result.payload.message == code_json.message
    assert code_result.input_format == "plain_text"
    assert not code_result.fallback_used


def test_client_override_resolution_is_deterministic() -> None:
    assert resolve_deep_normalization(True, "inherit") is True
    assert resolve_deep_normalization(False, "inherit") is False
    assert resolve_deep_normalization(False, "auto") is True
    assert resolve_deep_normalization(True, "off") is False


def test_p3_migration_owns_global_and_per_client_policy() -> None:
    migration = (
        ROOT / "migrations" / "versions" / "p3_0009_chat_intake_normalization.py"
    ).read_text(encoding="utf-8")
    guard = (ROOT / "backend" / "schema_guard.py").read_text(encoding="utf-8")

    assert 'revision = "p3_0009_chat_intake_normalization"' in migration
    assert 'down_revision = "p2i_0008_project_vector_routes"' in migration
    assert "CREATE TABLE IF NOT EXISTS chat_intake_settings" in migration
    assert "chat_deep_normalization_mode IN ('inherit', 'auto', 'off')" in migration
    assert 'CURRENT_REVISION = "p3_0009_chat_intake_normalization"' in guard
