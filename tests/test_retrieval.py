from __future__ import annotations

import unittest

from backend.retrieval import AdaptiveReranker
from backend.schemas import Source


def source(
    number: int,
    *,
    score: float,
    text: str,
    path: str,
) -> Source:
    return Source(
        document_id=f"document-{number}",
        chunk_id=f"chunk-{number}",
        path=path,
        text=text,
        score=score,
    )


class AdaptiveRerankerTests(unittest.TestCase):
    def make_reranker(
        self,
        *,
        min_sources: int = 2,
        max_sources: int = 4,
        max_context_chars: int = 4_000,
        min_score: float = 0.30,
        score_window: float = 0.20,
    ) -> AdaptiveReranker:
        return AdaptiveReranker(
            min_sources=min_sources,
            max_sources=max_sources,
            max_context_chars=max_context_chars,
            min_score=min_score,
            score_window=score_window,
        )

    def test_path_and_text_fit_can_rerank_vector_candidates(self) -> None:
        candidates = [
            source(
                1,
                score=0.70,
                text="Unrelated documentation",
                path="docs/overview.md",
            ),
            source(
                2,
                score=0.68,
                text="FastAPI main application entrypoint",
                path="fastapi/main.py",
            ),
        ]

        decision = self.make_reranker().select("FastAPI main", candidates)

        self.assertEqual(decision.sources[0].path, "fastapi/main.py")
        self.assertEqual(decision.candidate_count, 2)
        self.assertEqual(decision.selected_count, 2)

    def test_duplicate_chunks_are_removed(self) -> None:
        duplicate_text = "FastAPI application setup and startup"
        candidates = [
            source(1, score=0.82, text=duplicate_text, path="app.py"),
            source(2, score=0.81, text=duplicate_text, path="app.py"),
            source(
                3,
                score=0.78,
                text="Router registration",
                path="routes.py",
            ),
        ]

        decision = self.make_reranker().select("FastAPI startup", candidates)

        self.assertEqual(decision.reranked_count, 2)
        self.assertEqual(decision.selected_count, 2)

    def test_low_relevance_candidates_are_not_sent_to_generation(self) -> None:
        candidates = [
            source(
                1,
                score=0.12,
                text="Unrelated content",
                path="notes.txt",
            )
        ]

        decision = self.make_reranker().select("FastAPI startup", candidates)

        self.assertEqual(decision.sources, [])
        self.assertEqual(decision.selected_count, 0)

    def test_multilingual_copies_of_the_same_document_are_diversified(self) -> None:
        candidates = [
            source(
                1,
                score=0.82,
                text="FastAPI command line documentation in Korean",
                path="docs/ko/docs/fastapi-cli.md",
            ),
            source(
                2,
                score=0.81,
                text="FastAPI command line documentation in Japanese",
                path="docs/ja/docs/fastapi-cli.md",
            ),
            source(
                3,
                score=0.78,
                text="FastAPI manual deployment",
                path="docs/en/docs/deployment/manually.md",
            ),
        ]

        decision = self.make_reranker(
            min_sources=1,
            max_sources=4,
        ).select("FastAPI command line deployment", candidates)

        selected_paths = [item.path for item in decision.sources]
        self.assertEqual(decision.selected_count, 2)
        self.assertIn("docs/en/docs/deployment/manually.md", selected_paths)
        self.assertEqual(
            sum(path.endswith("fastapi-cli.md") for path in selected_paths),
            1,
        )

    def test_context_budget_applies_after_minimum_evidence(self) -> None:
        candidates = [
            source(
                number,
                score=0.90 - (number * 0.01),
                text=f"FastAPI evidence {number} " + ("x" * 1_000),
                path=f"file-{number}.py",
            )
            for number in range(4)
        ]

        decision = self.make_reranker(
            min_sources=1,
            max_sources=4,
            max_context_chars=2_000,
        ).select("FastAPI evidence", candidates)

        self.assertEqual(decision.selected_count, 1)
        self.assertLessEqual(decision.context_chars, 2_000)


if __name__ == "__main__":
    unittest.main()
