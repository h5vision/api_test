from __future__ import annotations

import unittest

from pydantic import ValidationError

from backend.schemas import ChatRequest


class VSCodeChatCompatibilityTests(unittest.TestCase):
    def test_frozen_chat_payload_is_preserved(self) -> None:
        payload = ChatRequest.model_validate(
            {
                "project_id": "h5vision/fest-api",
                "message": "실행 구조를 설명해줘",
                "session_id": "session-1",
                "history": [{"role": "user", "content": "이전 질문"}],
                "context": "README 본문",
            }
        )

        self.assertEqual(payload.project_id, "h5vision/fest-api")
        self.assertEqual(payload.session_id, "session-1")
        self.assertEqual(payload.history[0].content, "이전 질문")
        self.assertEqual(len(payload.context), 1)
        self.assertEqual(payload.context[0].name, "VS Code context")

    def test_serialized_tutorial_envelope_is_normalized(self) -> None:
        payload = ChatRequest.model_validate(
            {
                "request": {
                    "prompt": "경로는?",
                    "command": "explain",
                    "references": [
                        {
                            "id": "file:///README.md",
                            "name": "README.md",
                            "value": {"content": "# Project"},
                        }
                    ],
                },
                "chat_context": {
                    "history": [
                        {"prompt": "FastAPI 시작 파일을 찾아줘"},
                        {
                            "response": [
                                {"value": {"value": "app.py를 확인했습니다."}}
                            ]
                        },
                    ]
                },
                "workspace": {"name": "fest-api"},
                "future_field": {"enabled": True},
            }
        )

        self.assertEqual(payload.message, "경로는?")
        self.assertEqual(payload.project_id, "fest-api")
        self.assertTrue(payload.session_id.startswith("vscode-"))
        self.assertEqual(
            [(item.role, item.content) for item in payload.history],
            [
                ("user", "FastAPI 시작 파일을 찾아줘"),
                ("assistant", "app.py를 확인했습니다."),
            ],
        )
        self.assertEqual(len(payload.context), 4)
        self.assertEqual(payload.context[0].name, "README.md")
        self.assertIn("future_field", payload.context[-1].value["content"])
        self.assertIn("future_field", payload.model_extra)

    def test_prompt_only_payload_uses_safe_fallback_identifiers(self) -> None:
        payload = ChatRequest.model_validate({"prompt": "이 코드 설명해줘"})

        self.assertEqual(payload.project_id, "__auto__")
        self.assertTrue(payload.session_id.startswith("vscode-"))
        self.assertEqual(payload.message, "이 코드 설명해줘")

    def test_unknown_future_fields_do_not_break_the_contract(self) -> None:
        payload = ChatRequest.model_validate(
            {
                "prompt": "질문",
                "attempt": 2,
                "toolReferences": [{"name": "read_file"}],
            }
        )

        self.assertEqual(payload.model_extra["attempt"], 2)
        self.assertIn("toolReferences", payload.model_extra)

    def test_total_request_size_is_bounded(self) -> None:
        with self.assertRaisesRegex(ValidationError, "must not exceed 10 MB"):
            ChatRequest.model_validate(
                {
                    "prompt": "질문",
                    "future_field": "x" * 10_000_001,
                }
            )


if __name__ == "__main__":
    unittest.main()
