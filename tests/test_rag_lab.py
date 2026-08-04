from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from backend.rag_lab import RagLabClient


class _Response:
    def __init__(self, value: dict) -> None:
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.value).encode("utf-8")


class RagLabClientTests(unittest.TestCase):
    def test_prompt_preserves_messages_and_source_order(self) -> None:
        response = _Response(
            {
                "has_evidence": True,
                "messages": [
                    {"role": "system", "content": "system-owned-by-rag-lab"},
                    {"role": "user", "content": "rendered evidence"},
                ],
                "sources": [
                    {
                        "path": "src/first.py",
                        "text": "first",
                        "score": 0.9,
                        "line_start": 1,
                        "line_end": 2,
                    },
                    {
                        "path": "src/second.py",
                        "text": "second",
                        "score": 0.8,
                        "line_start": 3,
                        "line_end": 4,
                    },
                ],
                "top_score": 0.9,
                "threshold": 0.54,
            }
        )
        client = RagLabClient("http://192.0.2.12:8200")

        with patch("backend.rag_lab.urllib.request.urlopen", return_value=response):
            result = client.prompt("fest-api", "question")

        self.assertEqual(result.messages[0]["content"], "system-owned-by-rag-lab")
        self.assertEqual([item.path for item in result.sources], ["src/first.py", "src/second.py"])
        self.assertEqual([item.citation_id for item in result.sources], [1, 2])
        self.assertTrue(result.sources[0].document_id.startswith("ragdoc_"))
        self.assertTrue(result.sources[0].chunk_id.startswith("ragchunk_"))

    def test_prompt_without_evidence_keeps_no_sources(self) -> None:
        response = _Response(
            {
                "has_evidence": False,
                "messages": [
                    {"role": "system", "content": "return NO_EVIDENCE"},
                    {"role": "user", "content": "no project documents"},
                ],
                "sources": [],
                "top_score": 0.2,
                "threshold": 0.54,
            }
        )
        client = RagLabClient("http://192.0.2.12:8200")

        with patch("backend.rag_lab.urllib.request.urlopen", return_value=response):
            result = client.prompt("fest-api", "question")

        self.assertFalse(result.has_evidence)
        self.assertEqual(result.sources, [])


if __name__ == "__main__":
    unittest.main()
