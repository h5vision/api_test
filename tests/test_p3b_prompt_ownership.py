from __future__ import annotations

import unittest

from backend.generation import GenerationRouter, passthrough_messages
from backend.schemas import ChatContextItem, HistoryMessage
from backend.services import ServiceError


class P3BPromptOwnershipTests(unittest.TestCase):
    def test_direct_chat_contains_only_client_authored_messages(self) -> None:
        messages = passthrough_messages(
            "current question",
            [
                HistoryMessage(role="user", content="previous question"),
                HistoryMessage(role="assistant", content="previous answer"),
            ],
        )

        self.assertEqual(
            messages,
            [
                {"role": "user", "content": "previous question"},
                {"role": "assistant", "content": "previous answer"},
                {"role": "user", "content": "current question"},
            ],
        )
        self.assertNotIn("system", [item["role"] for item in messages])

    def test_external_vectordb_messages_are_preserved_exactly(self) -> None:
        messages = [
            {"role": "system", "content": "owned by external VectorDB"},
            {"role": "user", "content": "rendered evidence and question"},
        ]

        validated = GenerationRouter._validate_external_messages(messages)

        self.assertEqual(validated, messages)

    def test_client_text_attachment_is_forwarded_without_system_prompt(self) -> None:
        messages = passthrough_messages(
            "review this file",
            [],
            [
                ChatContextItem(
                    id="attachment:1",
                    name="README.md",
                    value={"kind": "text", "content": "# Vision"},
                )
            ],
        )

        self.assertEqual(messages[0]["role"], "user")
        self.assertIn("review this file", messages[0]["content"])
        self.assertIn("README.md", messages[0]["content"])
        self.assertIn("# Vision", messages[0]["content"])
        self.assertNotIn("system", [item["role"] for item in messages])

    def test_invalid_external_message_is_rejected(self) -> None:
        with self.assertRaises(ServiceError):
            GenerationRouter._validate_external_messages(
                [{"role": "tool", "content": "not supported"}]
            )


if __name__ == "__main__":
    unittest.main()
