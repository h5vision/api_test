from __future__ import annotations

import unittest

from pydantic import ValidationError

from backend.canonical_context import (
    CANONICAL_CONTEXT_SCHEMA_VERSION,
    CanonicalContext,
    CanonicalContextRetrieval,
    build_canonical_context,
)
from backend.schemas import Source


def _source(path: str = "src/main.py", text: str = "print('ok')") -> Source:
    return Source(
        document_id="doc-1",
        document_version_id="docv-1",
        chunk_id="chunk-1",
        path=path,
        language="python",
        line_start=1,
        line_end=1,
        text=text,
        score=0.91,
        metadata={"origin": "test"},
    )


class CanonicalContextTests(unittest.TestCase):
    def test_vector_prompt_context_preserves_messages_and_citation_order(self) -> None:
        context = build_canonical_context(
            request_id="req-1",
            client_id="client-1",
            project_id="h5vision/fest-api",
            snapshot_id="snap-1",
            session_id="session-1",
            query="entry point?",
            retrieval=CanonicalContextRetrieval(
                owner="vectordb",
                mode="prompt",
                prompt_owner="vectordb",
                provider="rag_lab",
                endpoint="http://192.168.0.12:8200/prompt",
                has_evidence=True,
                top_score=0.91,
                threshold=0.54,
            ),
            messages=[
                {"role": "system", "content": "use evidence"},
                {"role": "user", "content": "[1] src/main.py"},
            ],
            sources=[_source(), _source("src/other.py", "other")],
        )

        self.assertEqual(context.schema_version, CANONICAL_CONTEXT_SCHEMA_VERSION)
        self.assertRegex(context.context_id, r"^ctx_[0-9a-f]{32}$")
        self.assertEqual([item.citation_id for item in context.sources], [1, 2])
        self.assertEqual(context.messages[0].content, "use evidence")
        self.assertFalse(context.retention.raw_content_persisted)
        self.assertEqual(len(context.sources[0].content_sha256 or ""), 64)

    def test_context_id_is_deterministic_for_same_request_material(self) -> None:
        values = dict(
            request_id="req-1",
            client_id=None,
            project_id="project",
            snapshot_id="snapshot",
            session_id="session",
            query="question",
            retrieval=CanonicalContextRetrieval(
                owner="vision_legacy",
                mode="search",
                prompt_owner="vision_legacy",
                provider="qdrant",
                has_evidence=True,
            ),
            sources=[_source()],
        )
        first = build_canonical_context(**values)
        second = build_canonical_context(**values)
        self.assertEqual(first.context_id, second.context_id)

    def test_vector_prompt_requires_messages_when_evidence_exists(self) -> None:
        with self.assertRaises(ValidationError):
            build_canonical_context(
                request_id="req-1",
                client_id=None,
                project_id="project",
                snapshot_id="snapshot",
                session_id="session",
                query="question",
                retrieval=CanonicalContextRetrieval(
                    owner="vectordb",
                    mode="prompt",
                    prompt_owner="vectordb",
                    provider="rag_lab",
                    has_evidence=True,
                ),
                sources=[_source()],
            )

    def test_vectordb_search_is_rejected_as_p3_boundary(self) -> None:
        with self.assertRaises(ValidationError):
            CanonicalContextRetrieval(
                owner="vectordb",
                mode="search",
                prompt_owner="none",
                provider="rag_lab",
                has_evidence=False,
            )

    def test_cross_snapshot_source_is_rejected(self) -> None:
        valid = build_canonical_context(
            request_id="req-1",
            client_id=None,
            project_id="project",
            snapshot_id="snapshot",
            session_id="session",
            query="question",
            retrieval=CanonicalContextRetrieval(
                owner="vision_legacy",
                mode="search",
                prompt_owner="vision_legacy",
                provider="qdrant",
                has_evidence=True,
            ),
            sources=[_source()],
        )
        data = valid.model_dump(mode="json")
        data["sources"][0]["snapshot_id"] = "different-snapshot"
        with self.assertRaises(ValidationError):
            CanonicalContext.model_validate(data)

    def test_no_evidence_context_cannot_contain_sources(self) -> None:
        with self.assertRaises(ValidationError):
            build_canonical_context(
                request_id="req-1",
                client_id=None,
                project_id="project",
                snapshot_id="snapshot",
                session_id="session",
                query="question",
                retrieval=CanonicalContextRetrieval(
                    owner="vision_legacy",
                    mode="search",
                    prompt_owner="vision_legacy",
                    provider="qdrant",
                    has_evidence=False,
                ),
                sources=[_source()],
            )


if __name__ == "__main__":
    unittest.main()
