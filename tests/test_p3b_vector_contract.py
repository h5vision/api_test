from __future__ import annotations

import unittest

from pydantic import ValidationError

from backend.vector_contract import (
    ExternalVectorIndexDescriptor,
    ExternalVectorPromptResponse,
    vector_service_contract,
)


def _capabilities() -> dict:
    return {
        "schema_version": "1.0",
        "service_id": "external-rag",
        "conformance_level": "L2",
        "prompt": True,
        "search": True,
        "snapshot_filter": True,
        "source_hydration": True,
        "incremental_indexing": False,
    }


class P3BVectorContractTests(unittest.TestCase):
    def test_contract_exposes_l2_prompt_schemas(self) -> None:
        contract = vector_service_contract()

        self.assertEqual(contract["minimum_chat_conformance"], "L2")
        self.assertIn("POST /prompt", contract["required_endpoints"])
        self.assertIn("index_descriptor", contract["schemas"])
        self.assertIn("prompt_response", contract["schemas"])

    def test_ready_index_requires_snapshot_aware_l2(self) -> None:
        descriptor = ExternalVectorIndexDescriptor.model_validate(
            {
                "schema_version": "1.0",
                "index_id": "index-1",
                "project_id": "h5vision/fest-api",
                "snapshot_id": "snapshot-1",
                "source_revision": "afe41126",
                "manifest_sha256": "a" * 64,
                "index_version": "2026-08-10.1",
                "embedding": {
                    "provider": "ollama",
                    "model": "bge-m3",
                    "dimension": 1024,
                    "distance_metric": "cosine",
                },
                "status": "ready",
                "capabilities": _capabilities(),
            }
        )

        self.assertEqual(descriptor.snapshot_id, "snapshot-1")

    def test_prompt_response_rejects_cross_snapshot_source(self) -> None:
        with self.assertRaises(ValidationError):
            ExternalVectorPromptResponse.model_validate(
                {
                    "schema_version": "1.0",
                    "project_id": "h5vision/fest-api",
                    "snapshot_id": "snapshot-1",
                    "index_id": "index-1",
                    "index_version": "1",
                    "has_evidence": True,
                    "messages": [
                        {"role": "user", "content": "rendered evidence"}
                    ],
                    "sources": [
                        {
                            "citation_id": 1,
                            "document_id": "doc-1",
                            "chunk_id": "chunk-1",
                            "project_id": "h5vision/fest-api",
                            "snapshot_id": "snapshot-2",
                            "score": 0.9,
                            "locator": {"path": "backend/app.py"},
                        }
                    ],
                }
            )


if __name__ == "__main__":
    unittest.main()
