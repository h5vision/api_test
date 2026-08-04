from __future__ import annotations

import unittest

from backend.chat_progress import simulated_chat_progress


class ChatProgressTests(unittest.TestCase):
    def test_each_sse_event_has_the_requested_display_state(self) -> None:
        expected = [
            ("meta", "sending", "전송중"),
            ("status", "reasoning", "추론중"),
            ("delta", "thinking", "생각중"),
            ("done", "answering", "답변중"),
            ("error", "failed", "답변 실패"),
        ]

        for event_name, stage, label in expected:
            with self.subTest(event=event_name):
                progress = simulated_chat_progress("req_test", event_name)
                self.assertEqual(progress["request_id"], "req_test")
                self.assertEqual(progress["stage"], stage)
                self.assertEqual(progress["label"], label)
                self.assertTrue(progress["simulated"])
                self.assertEqual(progress["progress_source"], "vision-generator")
                self.assertTrue(progress["occurred_at"].endswith("+00:00"))


if __name__ == "__main__":
    unittest.main()
