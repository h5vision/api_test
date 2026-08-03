from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass

from .retrieval import AdaptiveReranker, RetrievalDecision
from .schemas import ChatContextItem, HistoryMessage, SearchResponse, Source


_TERM_PATTERN = re.compile(r"[a-z0-9_./:-]{2,}|[가-힣]{2,}")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_FOLLOW_UP_PATTERN = re.compile(
    r"(?:"
    r"^(?:그|이|저)(?:거|것|게|파일|코드|함수|클래스|경로|부분|내용|프로젝트)"
    r"|(?:경로|위치|이름|원인|이유|방법|차이|내용)(?:은|는|이|가|를|을)?\??$"
    r"|^(?:왜|어디|어떻게|뭐|무엇|누구)(?:야|지|인가|해|돼|되나|죠)?\??$"
    r"|(?:다시|더|계속|이어서)\s*(?:찾아|검색|설명|확인|분석|해줘|봐)?"
    r"|^(?:what about|where|why|how about|find it|search again)\b"
    r")",
    re.IGNORECASE,
)
_CONTEXT_REFERENCE_PATTERN = re.compile(
    r"(?:이|그|현재|첨부|선택한)\s*(?:파일|코드|함수|클래스|내용|부분|문서)"
    r"|(?:첨부파일|첨부한|선택 영역|this (?:file|code|function)|attached)",
    re.IGNORECASE,
)
_COMPLEX_QUERY_PATTERN = re.compile(
    r"(?:비교|분석|원인|가능성|예외|반례|전체\s*흐름|장단점|설계|영향|문제점|"
    r"여러\s*(?:경우|방법)|root cause|compare|analy[sz]e|alternatives?|edge cases?)",
    re.IGNORECASE,
)
_QUERY_STOP_TERMS = {
    "관련",
    "후속",
    "질문",
    "설명해줘",
    "알려줘",
    "찾아줘",
    "확인해줘",
    "please",
    "about",
}


@dataclass(frozen=True)
class AgenticQueryPlan:
    requested_mode: str
    mode: str
    original_query: str
    standalone_query: str
    queries: tuple[str, ...]
    follow_up: bool
    context_grounded: bool
    max_steps: int
    minimum_steps: int


@dataclass(frozen=True)
class AgenticRetrievalTrace:
    requested_mode: str
    mode: str
    original_query: str
    standalone_query: str
    queries: tuple[str, ...]
    follow_up_rewritten: bool
    context_grounded: bool
    step_count: int
    max_steps: int
    candidate_count: int
    unique_candidate_count: int
    evidence_coverage: float
    final_novelty_ratio: float
    stop_reason: str


@dataclass(frozen=True)
class AgenticRetrievalResult:
    decision: RetrievalDecision
    trace: AgenticRetrievalTrace
    embedding_provider: str


class ConversationAwareQueryPlanner:
    """Builds bounded retrieval queries without spending an LLM call."""

    def __init__(self, *, balanced_steps: int = 2, deep_steps: int = 3) -> None:
        self.balanced_steps = max(1, min(4, balanced_steps))
        self.deep_steps = max(self.balanced_steps, min(6, deep_steps))

    @staticmethod
    def _latest_user_message(history: list[HistoryMessage]) -> str | None:
        for item in reversed(history):
            if item.role == "user" and item.content.strip():
                return item.content.strip()
        return None

    @staticmethod
    def _is_follow_up(query: str, previous_user: str | None) -> bool:
        if previous_user is None:
            return False
        normalized = query.strip()
        return bool(_FOLLOW_UP_PATTERN.search(normalized)) or len(normalized) <= 12

    @staticmethod
    def _keyword_query(value: str) -> str:
        terms: list[str] = []
        seen: set[str] = set()
        for term in _TERM_PATTERN.findall(value.lower()):
            if term in _QUERY_STOP_TERMS or term in seen:
                continue
            seen.add(term)
            terms.append(term)
            if len(terms) >= 16:
                break
        return " ".join(terms)

    @staticmethod
    def _unique_queries(values: list[str], limit: int) -> tuple[str, ...]:
        queries: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = _WHITESPACE_PATTERN.sub(" ", value).strip()
            key = normalized.casefold()
            if not normalized or key in seen:
                continue
            seen.add(key)
            queries.append(normalized)
            if len(queries) >= limit:
                break
        return tuple(queries)

    @staticmethod
    def _automatic_mode(
        query: str,
        *,
        follow_up: bool,
        context_grounded: bool,
        has_history: bool,
    ) -> str:
        if _COMPLEX_QUERY_PATTERN.search(query) or len(query) >= 180:
            return "deep"
        if follow_up or context_grounded or has_history or len(query) >= 60:
            return "balanced"
        return "fast"

    @staticmethod
    def context_hint(
        frontend_context: str | list[ChatContextItem],
        *,
        max_chars: int = 1_200,
    ) -> str:
        if isinstance(frontend_context, str):
            return _WHITESPACE_PATTERN.sub(" ", frontend_context).strip()[:max_chars]

        parts: list[str] = []
        remaining = max_chars
        for item in frontend_context[:5]:
            if item.id == "vscode.extra" or (item.name or "").startswith("prompt:"):
                continue
            label = (item.name or item.id or "첨부").strip()
            content = ""
            if isinstance(item.value, str):
                content = item.value
            elif isinstance(item.value, dict):
                for key in ("content", "text", "selection", "code"):
                    candidate = item.value.get(key)
                    if isinstance(candidate, str) and candidate.strip():
                        content = candidate
                        break
            part = f"{label} {content}".strip()
            if not part:
                continue
            clipped = _WHITESPACE_PATTERN.sub(" ", part).strip()[:remaining]
            if clipped:
                parts.append(clipped)
                remaining -= len(clipped)
            if remaining <= 0:
                break
        return " ".join(parts)

    def plan(
        self,
        query: str,
        history: list[HistoryMessage],
        mode: str,
        frontend_context: str | list[ChatContextItem] = "",
    ) -> AgenticQueryPlan:
        requested_mode = (
            mode if mode in {"auto", "fast", "balanced", "deep"} else "auto"
        )
        original_query = _WHITESPACE_PATTERN.sub(" ", query).strip()
        previous_user = self._latest_user_message(history)
        follow_up = self._is_follow_up(original_query, previous_user)
        standalone_query = (
            f"{previous_user}에 이어서 묻는 질문: {original_query}"
            if follow_up and previous_user
            else original_query
        )
        context_hint = self.context_hint(frontend_context)
        context_grounded = bool(
            context_hint and _CONTEXT_REFERENCE_PATTERN.search(original_query)
        )
        if context_grounded:
            standalone_query = (
                f"{standalone_query} 첨부 컨텍스트: {context_hint}"
            )
        normalized_mode = (
            self._automatic_mode(
                original_query,
                follow_up=follow_up,
                context_grounded=context_grounded,
                has_history=bool(history),
            )
            if requested_mode == "auto"
            else requested_mode
        )

        if normalized_mode == "fast":
            # Fast keeps the original one-search latency budget, while still
            # making short follow-up questions retrievable in conversation.
            queries = (standalone_query,)
            max_steps = 1
            minimum_steps = 1
        elif normalized_mode == "balanced":
            max_steps = self.balanced_steps
            keyword_query = self._keyword_query(standalone_query)
            queries = self._unique_queries(
                [standalone_query, keyword_query, original_query],
                max_steps,
            )
            minimum_steps = 1
        else:
            max_steps = self.deep_steps
            keyword_query = self._keyword_query(standalone_query)
            queries = self._unique_queries(
                [
                    standalone_query,
                    keyword_query,
                    original_query,
                ],
                max_steps,
            )
            minimum_steps = min(2, len(queries))

        return AgenticQueryPlan(
            requested_mode=requested_mode,
            mode=normalized_mode,
            original_query=original_query,
            standalone_query=standalone_query,
            queries=queries,
            follow_up=follow_up,
            context_grounded=context_grounded,
            max_steps=max_steps,
            minimum_steps=minimum_steps,
        )


class EvidenceLedger:
    """Accumulates unique evidence across retrieval steps."""

    def __init__(self) -> None:
        self._by_identity: dict[str, Source] = {}
        self._text_fingerprints: set[str] = set()

    @staticmethod
    def _identity(source: Source) -> str:
        return "|".join(
            (
                source.document_id,
                source.document_version_id or "",
                source.chunk_id,
                source.path or "",
            )
        ).casefold()

    @staticmethod
    def _text_fingerprint(source: Source) -> str:
        normalized = _WHITESPACE_PATTERN.sub(" ", source.text).strip().casefold()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def add(self, sources: list[Source]) -> int:
        novel_count = 0
        for source in sources:
            identity = self._identity(source)
            fingerprint = self._text_fingerprint(source)
            existing = self._by_identity.get(identity)
            if existing is not None:
                if source.score > existing.score:
                    self._by_identity[identity] = source
                continue
            if fingerprint in self._text_fingerprints:
                continue
            self._by_identity[identity] = source
            self._text_fingerprints.add(fingerprint)
            novel_count += 1
        return novel_count

    @property
    def sources(self) -> list[Source]:
        return list(self._by_identity.values())

    def __len__(self) -> int:
        return len(self._by_identity)


class AgenticRAGOrchestrator:
    """Runs a budgeted retrieve/evaluate/refine loop for low-cost models."""

    def __init__(
        self,
        reranker: AdaptiveReranker,
        *,
        min_evidence: int,
        min_coverage: float = 0.55,
        min_novelty_ratio: float = 0.10,
    ) -> None:
        self.reranker = reranker
        self.min_evidence = max(1, min_evidence)
        self.min_coverage = min(1.0, max(0.0, min_coverage))
        self.min_novelty_ratio = min(1.0, max(0.0, min_novelty_ratio))

    @staticmethod
    def _terms(value: str) -> set[str]:
        return set(_TERM_PATTERN.findall(value.casefold())) - _QUERY_STOP_TERMS

    def _coverage(self, query: str, sources: list[Source]) -> float:
        if not sources:
            return 0.0
        query_terms = self._terms(query)
        evidence_terms: set[str] = set()
        for source in sources:
            evidence_terms.update(self._terms(source.path or ""))
            evidence_terms.update(self._terms(source.text[:8_000]))
        lexical = (
            len(query_terms & evidence_terms) / len(query_terms)
            if query_terms
            else 0.0
        )
        similarity = max(0.0, min(1.0, max(source.score for source in sources)))
        breadth = min(1.0, len(sources) / self.min_evidence)
        return round((0.45 * lexical) + (0.35 * similarity) + (0.20 * breadth), 4)

    def run(
        self,
        plan: AgenticQueryPlan,
        search: Callable[[str], SearchResponse],
    ) -> AgenticRetrievalResult:
        ledger = EvidenceLedger()
        decision = self.reranker.select(plan.standalone_query, [])
        executed_queries: list[str] = []
        total_candidates = 0
        final_novelty_ratio = 0.0
        coverage = 0.0
        stop_reason = "query_budget_exhausted"
        embedding_provider = "unknown"

        for step, query in enumerate(plan.queries[: plan.max_steps], start=1):
            response = search(query)
            embedding_provider = response.embedding_provider
            executed_queries.append(query)
            total_candidates += len(response.results)
            novel_count = ledger.add(response.results)
            final_novelty_ratio = (
                novel_count / len(response.results) if response.results else 0.0
            )
            base_decision = self.reranker.select(
                plan.standalone_query,
                ledger.sources,
            )
            decision = RetrievalDecision(
                sources=base_decision.sources,
                candidate_count=total_candidates,
                reranked_count=base_decision.reranked_count,
                selected_count=base_decision.selected_count,
                context_chars=base_decision.context_chars,
                policy=f"agentic-rag-v1/{plan.mode}",
            )
            coverage = self._coverage(plan.standalone_query, decision.sources)

            if step < plan.minimum_steps:
                continue
            if plan.mode == "fast":
                stop_reason = "fast_single_step"
                break
            if len(decision.sources) >= self.min_evidence and coverage >= self.min_coverage:
                stop_reason = "sufficient_evidence"
                break
            if step > 1 and final_novelty_ratio < self.min_novelty_ratio:
                stop_reason = "low_evidence_novelty"
                break
        else:
            if len(executed_queries) < plan.max_steps:
                stop_reason = "no_more_query_variants"

        trace = AgenticRetrievalTrace(
            requested_mode=plan.requested_mode,
            mode=plan.mode,
            original_query=plan.original_query,
            standalone_query=plan.standalone_query,
            queries=tuple(executed_queries),
            follow_up_rewritten=plan.follow_up,
            context_grounded=plan.context_grounded,
            step_count=len(executed_queries),
            max_steps=plan.max_steps,
            candidate_count=total_candidates,
            unique_candidate_count=len(ledger),
            evidence_coverage=coverage,
            final_novelty_ratio=round(final_novelty_ratio, 4),
            stop_reason=stop_reason,
        )
        return AgenticRetrievalResult(
            decision=decision,
            trace=trace,
            embedding_provider=embedding_provider,
        )

    def fallback(
        self,
        plan: AgenticQueryPlan,
        *,
        stop_reason: str,
        embedding_provider: str = "unavailable",
    ) -> AgenticRetrievalResult:
        decision = RetrievalDecision(
            sources=[],
            candidate_count=0,
            reranked_count=0,
            selected_count=0,
            context_chars=0,
            policy=f"agentic-rag-v1/{plan.mode}/context-fallback",
        )
        return AgenticRetrievalResult(
            decision=decision,
            trace=AgenticRetrievalTrace(
                requested_mode=plan.requested_mode,
                mode=plan.mode,
                original_query=plan.original_query,
                standalone_query=plan.standalone_query,
                queries=(),
                follow_up_rewritten=plan.follow_up,
                context_grounded=plan.context_grounded,
                step_count=0,
                max_steps=plan.max_steps,
                candidate_count=0,
                unique_candidate_count=0,
                evidence_coverage=0.0,
                final_novelty_ratio=0.0,
                stop_reason=stop_reason,
            ),
            embedding_provider=embedding_provider,
        )
