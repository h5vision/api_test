from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from backend.rag_lab import RagLabClient, RagLabError


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
    def test_briefing_validates_and_returns_generated_artifact(self) -> None:
        response = _Response(
            {
                "project_id": "fastapi-cli",
                "briefing": "## 이 프로젝트는\nFastAPI CLI입니다.",
                "references": [{"n": 1, "path": "README.md"}],
                "reference_files": [{"path": "README.md"}],
                "mentioned_files": [],
                "structure": {"total_files": 92},
                "index_commit": "fb2dd86bf442",
                "outdated": False,
                "ok": True,
            }
        )
        client = RagLabClient("http://192.0.2.12:8200")

        with patch("backend.rag_lab.urllib.request.urlopen", return_value=response) as request:
            result = client.briefing("fastapi-cli")

        self.assertEqual(result["project_id"], "fastapi-cli")
        self.assertIn("FastAPI CLI", result["briefing"])
        self.assertIn("project_id=fastapi-cli", request.call_args.args[0].full_url)

    def test_briefing_rejects_mismatched_project(self) -> None:
        client = RagLabClient("http://192.0.2.12:8200")
        response = _Response({"project_id": "other", "briefing": "content"})

        with patch("backend.rag_lab.urllib.request.urlopen", return_value=response):
            with self.assertRaises(RagLabError):
                client.briefing("fastapi-cli")

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

    def test_project_resolution_prefers_unique_snapshot_revision(self) -> None:
        response = _Response(
            {
                "projects": [
                    {
                        "project_id": "fest-api-v2",
                        "state": "done",
                        "commit": "afe41126",
                        "indexed_at": "2026-08-06T14:39:24+09:00",
                        "fingerprint": {"embed_model": "bge-m3:latest"},
                    },
                    {
                        "project_id": "fest-api-old",
                        "state": "done",
                        "commit": "previous",
                    },
                ]
            }
        )
        client = RagLabClient("http://192.0.2.12:8200")

        with patch("backend.rag_lab.urllib.request.urlopen", return_value=response):
            binding = client.resolve_project(
                "h5vision/fest-api",
                revision="afe41126",
            )

        self.assertEqual(binding.external_project_id, "fest-api-v2")
        self.assertEqual(binding.binding_strength, "revision_matched")
        self.assertEqual(binding.verification_state, "compatible")

    def test_project_resolution_rejects_exact_project_with_wrong_revision(self) -> None:
        response = _Response(
            {
                "projects": [
                    {
                        "project_id": "fest-api",
                        "state": "done",
                        "commit": "old-revision",
                    }
                ]
            }
        )
        client = RagLabClient("http://192.0.2.12:8200")

        with patch("backend.rag_lab.urllib.request.urlopen", return_value=response):
            with self.assertRaises(RagLabError):
                client.resolve_project("h5vision/fest-api", revision="new-revision")


if __name__ == "__main__":
    unittest.main()
