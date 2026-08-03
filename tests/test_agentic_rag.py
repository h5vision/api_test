from __future__ import annotations

import unittest

from backend.agentic_rag import (
    AgenticRAGOrchestrator,
    ConversationAwareQueryPlanner,
    EvidenceLedger,
)
from backend.retrieval import AdaptiveReranker
from backend.schemas import ChatContextItem, HistoryMessage, SearchResponse, Source


def source(
    number: int,
    *,
    score: float = 0.80,
    text: str = "FastAPI application entrypoint",
    path: str = "backend/app.py",
) -> Source:
    return Source(
        document_id=f"document-{number}",
        chunk_id=f"chunk-{number}",
        path=path,
        text=text,
        score=score,
    )


def response(query: str, results: list[Source]) -> SearchResponse:
    return SearchResponse(
        project_id="h5vision/fest-api",
        query=query,
        results=results,
        embedding_provider="ollama",
    )


class ConversationAwareQueryPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = ConversationAwareQueryPlanner(
            balanced_steps=2,
            deep_steps=3,
        )
        self.history = [
            HistoryMessage(
                role="user",
                content="FastAPI 서버의 시작 파일을 찾아줘",
            ),
            HistoryMessage(
                role="assistant",
                content="시작 파일을 확인하겠습니다.",
            ),
        ]

    def test_short_follow_up_is_rewritten_with_previous_user_topic(self) -> None:
        plan = self.planner.plan("경로는?", self.history, "balanced")

        self.assertTrue(plan.follow_up)
        self.assertIn("FastAPI 서버의 시작 파일", plan.standalone_query)
        self.assertIn("경로는?", plan.standalone_query)
        self.assertEqual(plan.queries[0], plan.standalone_query)

    def test_fast_mode_keeps_one_search_but_uses_rewritten_query(self) -> None:
        plan = self.planner.plan("다시 찾아", self.history, "fast")

        self.assertEqual(plan.max_steps, 1)
        self.assertEqual(plan.queries, (plan.standalone_query,))
        self.assertNotEqual(plan.queries[0], plan.original_query)

    def test_standalone_question_is_not_rewritten(self) -> None:
        query = "FastAPI dependency injection 구조를 설명해줘"
        plan = self.planner.plan(query, self.history, "balanced")

        self.assertFalse(plan.follow_up)
        self.assertEqual(plan.standalone_query, query)
        self.assertEqual(plan.queries[0], query)
        self.assertLessEqual(len(plan.queries), 2)

    def test_attached_code_reference_is_added_to_retrieval_query(self) -> None:
        context = [
            ChatContextItem(
                id="file:///workspace/auth.py",
                name="auth.py",
                value={"content": "def authenticate(token): return verify(token)"},
            )
        ]

        plan = self.planner.plan(
            "이 함수가 어디에서 호출돼?",
            [],
            "fast",
            context,
        )

        self.assertTrue(plan.context_grounded)
        self.assertIn("auth.py", plan.standalone_query)
        self.assertIn("authenticate", plan.standalone_query)

    def test_auto_mode_keeps_simple_question_fast(self) -> None:
        plan = self.planner.plan("진입점은?", [], "auto")

        self.assertEqual(plan.requested_mode, "auto")
        self.assertEqual(plan.mode, "fast")

    def test_auto_mode_uses_deep_for_multi_case_analysis(self) -> None:
        plan = self.planner.plan(
            "이 장애의 가능한 원인과 예외 상황을 비교 분석해줘",
            [],
            "auto",
        )

        self.assertEqual(plan.requested_mode, "auto")
        self.assertEqual(plan.mode, "deep")


class EvidenceLedgerTests(unittest.TestCase):
    def test_duplicate_text_from_multiple_queries_is_added_once(self) -> None:
        ledger = EvidenceLedger()

        first = ledger.add([source(1)])
        duplicate = ledger.add([source(2)])

        self.assertEqual(first, 1)
        self.assertEqual(duplicate, 0)
        self.assertEqual(len(ledger), 1)


class AgenticRAGOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        reranker = AdaptiveReranker(
            min_sources=1,
            max_sources=4,
            max_context_chars=4_000,
            min_score=0.30,
            score_window=0.20,
        )
        self.orchestrator = AgenticRAGOrchestrator(
            reranker,
            min_evidence=1,
            min_coverage=0.55,
            min_novelty_ratio=0.10,
        )
        self.planner = ConversationAwareQueryPlanner(
            balanced_steps=2,
            deep_steps=3,
        )

    def test_balanced_mode_retries_when_first_evidence_is_insufficient(self) -> None:
        history = [
            HistoryMessage(role="user", content="FastAPI 시작 파일을 찾아줘")
        ]
        plan = self.planner.plan("경로는?", history, "balanced")
        calls: list[str] = []

        def search(query: str) -> SearchResponse:
            calls.append(query)
            if len(calls) == 1:
                return response(query, [source(1, score=0.10, text="irrelevant")])
            return response(query, [source(2, score=0.90)])

        result = self.orchestrator.run(plan, search)

        self.assertEqual(result.trace.step_count, 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.decision.selected_count, 1)
        self.assertEqual(result.embedding_provider, "ollama")

    def test_deep_mode_stops_when_repeated_query_adds_no_new_evidence(self) -> None:
        strict = AgenticRAGOrchestrator(
            self.orchestrator.reranker,
            min_evidence=2,
            min_coverage=0.99,
            min_novelty_ratio=0.10,
        )
        history = [HistoryMessage(role="user", content="FastAPI 시작 파일")]
        plan = self.planner.plan("다시 찾아", history, "deep")
        repeated = source(1)

        result = strict.run(
            plan,
            lambda query: response(query, [repeated]),
        )

        self.assertEqual(result.trace.step_count, 2)
        self.assertEqual(result.trace.final_novelty_ratio, 0.0)
        self.assertEqual(result.trace.stop_reason, "low_evidence_novelty")
        self.assertEqual(result.trace.unique_candidate_count, 1)

    def test_context_fallback_returns_a_trace_without_fake_sources(self) -> None:
        plan = self.planner.plan("이 코드 설명해줘", [], "balanced")

        result = self.orchestrator.fallback(
            plan,
            stop_reason="vector_unavailable_context_fallback",
        )

        self.assertEqual(result.decision.sources, [])
        self.assertEqual(result.trace.step_count, 0)
        self.assertEqual(
            result.trace.stop_reason,
            "vector_unavailable_context_fallback",
        )
        self.assertIn("context-fallback", result.decision.policy)


if __name__ == "__main__":
    unittest.main()
