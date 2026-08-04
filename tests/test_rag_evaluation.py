from __future__ import annotations

import unittest

from backend.rag_evaluation import RetrievalEvaluationCase, evaluate_retrieval
from backend.schemas import Source


class RetrievalEvaluationTests(unittest.TestCase):
    def test_quality_metrics_detect_wrong_language_and_junk(self) -> None:
        case = RetrievalEvaluationCase(
            name="security-code",
            query="인증 취약점",
            expected_path_patterns=(r"backend/.*\.py$",),
        )
        sources = [
            Source(
                document_id="doc-1",
                document_version_id="version-1",
                chunk_id="chunk-1",
                path="backend/security.py",
                line_start=1,
                line_end=5,
                text="def validate_token(token): return verify(token)",
                score=0.8,
            ),
            Source(
                document_id="doc-2",
                chunk_id="chunk-2",
                path="docs/ja/docs/security.md",
                text="''' });",
                score=0.7,
            ),
        ]

        result = evaluate_retrieval(case, sources)

        self.assertTrue(result.hit_at_k)
        self.assertEqual(result.precision_at_k, 0.5)
        self.assertEqual(result.junk_chunk_rate, 0.5)
        self.assertEqual(result.wrong_language_rate, 0.5)
        self.assertEqual(result.citation_ready_rate, 0.5)


if __name__ == "__main__":
    unittest.main()
