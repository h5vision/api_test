from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.connectivity import group_chat_session_rows
from backend.schemas import ChatSessionListResponse, ChatSessionUser


def test_chat_history_is_grouped_by_declared_user_and_session() -> None:
    now = datetime.now(timezone.utc)
    rows = [
        {
            "request_id": "req-2",
            "received_at": now,
            "completed_at": now,
            "client_id": "fcli-1",
            "client_name": "VS Code",
            "declared_user": "김개발",
            "project_id": "Vision",
            "session_id": "session-a",
            "requested_model_id": "auto",
            "used_model_id": "backendai:qwen",
            "provider": "backendai",
            "message": "두 번째 질문",
            "answer": "두 번째 답변",
            "status": "success",
            "status_code": 200,
        },
        {
            "request_id": "req-1",
            "received_at": now - timedelta(minutes=1),
            "completed_at": now - timedelta(minutes=1),
            "client_id": "fcli-1",
            "client_name": "VS Code",
            "declared_user": "김개발",
            "project_id": "Vision",
            "session_id": "session-a",
            "requested_model_id": "auto",
            "used_model_id": "backendai:qwen",
            "provider": "backendai",
            "message": "첫 번째 질문",
            "answer": "첫 번째 답변",
            "status": "success",
            "status_code": 200,
        },
    ]

    grouped = group_chat_session_rows(rows)

    assert len(grouped) == 1
    assert grouped[0]["display_name"] == "김개발"
    assert grouped[0]["sessions"][0]["title"] == "첫 번째 질문"
    assert [
        message["request_id"]
        for message in grouped[0]["sessions"][0]["messages"]
    ] == ["req-1", "req-2"]
    ChatSessionListResponse(
        checked_at=now,
        users=[ChatSessionUser(**grouped[0])],
        total_users=1,
        total_sessions=1,
    )


def test_admin_playground_user_is_decoded_from_client_id() -> None:
    now = datetime.now(timezone.utc)
    grouped = group_chat_session_rows(
        [
            {
                "request_id": "req-admin",
                "received_at": now,
                "client_id": "admin-playground:%EA%B4%80%EB%A6%AC%EC%9E%90",
                "project_id": "__unscoped__",
                "session_id": "playground-1",
                "message": "안녕",
                "answer": "반갑습니다",
                "status": "success",
            }
        ]
    )

    assert grouped[0]["display_name"] == "관리자"
    assert grouped[0]["sessions"][0]["messages"][0]["answer"] == "반갑습니다"
