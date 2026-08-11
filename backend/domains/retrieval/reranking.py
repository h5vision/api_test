from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass

from .schemas import Source


_TERM_PATTERN = re.compile(r"[a-z0-9_./:-]{2,}|[가-힣]{2,}")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_MULTILINGUAL_DOCS_PATTERN = re.compile(
    r"^docs/[^/]+/docs/(.+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RetrievalDecision:
    """Adaptive source selection result for one RAG request."""

    sources: list[Source]
    candidate_count: int
    reranked_count: int
    selected_count: int
    context_chars: int
    policy: str = "adaptive-rerank-v1"


class AdaptiveReranker:
    """Selects evidence using vector score, lexical fit, diversity and budget."""

    def __init__(
        self,
        *,
        min_sources: int,
        max_sources: int,
        max_context_chars: int,
        min_score: float,
        score_window: float,
    ) -> None:
        self.min_sources = max(1, min_sources)
        self.max_sources = max(self.min_sources, max_sources)
        self.max_context_chars = max(2_000, max_context_chars)
        self.min_score = min_score
        self.score_window = max(0.0, score_window)

    @staticmethod
    def _terms(value: str) -> set[str]:
        return set(_TERM_PATTERN.findall(value.lower()))

    @staticmethod
    def _fingerprint(source: Source) -> str:
        normalized = _WHITESPACE_PATTERN.sub(" ", source.text).strip().lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _source_chars(source: Source) -> int:
        label = source.path or source.document_id
        return len(label) + len(source.text) + 24

    @staticmethod
    def _translated_document_family(source: Source) -> str | None:
        match = _MULTILINGUAL_DOCS_PATTERN.match(source.path or "")
        return match.group(1).lower() if match is not None else None

    def _rerank_score(self, query_terms: set[str], source: Source) -> float:
        if not query_terms:
            return source.score
        text_terms = self._terms(source.text[:8_000])
        path_terms = self._terms(source.path or "")
        lexical_overlap = len(query_terms & text_terms) / len(query_terms)
        path_overlap = len(query_terms & path_terms) / len(query_terms)
        return source.score + (0.08 * lexical_overlap) + (0.04 * path_overlap)

    def select(self, query: str, candidates: list[Source]) -> RetrievalDecision:
        query_terms = self._terms(query)
        ranked = sorted(
            (
                (self._rerank_score(query_terms, source), source)
                for source in candidates
            ),
            key=lambda item: item[0],
            reverse=True,
        )

        deduplicated: list[tuple[float, Source]] = []
        seen_fingerprints: set[str] = set()
        seen_translated_families: set[str] = set()
        per_path: defaultdict[str, int] = defaultdict(int)
        for rank_score, source in ranked:
            fingerprint = self._fingerprint(source)
            if fingerprint in seen_fingerprints:
                continue
            translated_family = self._translated_document_family(source)
            if (
                translated_family is not None
                and translated_family in seen_translated_families
            ):
                continue
            path_key = (source.path or source.document_id).lower()
            if per_path[path_key] >= 2:
                continue
            seen_fingerprints.add(fingerprint)
            if translated_family is not None:
                seen_translated_families.add(translated_family)
            per_path[path_key] += 1
            deduplicated.append((rank_score, source))

        if not deduplicated or deduplicated[0][0] < self.min_score:
            return RetrievalDecision(
                sources=[],
                candidate_count=len(candidates),
                reranked_count=len(deduplicated),
                selected_count=0,
                context_chars=0,
            )

        top_score = deduplicated[0][0]
        score_floor = max(self.min_score, top_score - self.score_window)
        selected: list[Source] = []
        context_chars = 0

        for rank_score, source in deduplicated:
            if len(selected) >= self.max_sources:
                break
            if len(selected) >= self.min_sources and rank_score < score_floor:
                break

            source_chars = self._source_chars(source)
            if (
                selected
                and len(selected) >= self.min_sources
                and context_chars + source_chars > self.max_context_chars
            ):
                continue

            selected.append(source)
            context_chars += source_chars

        return RetrievalDecision(
            sources=selected,
            candidate_count=len(candidates),
            reranked_count=len(deduplicated),
            selected_count=len(selected),
            context_chars=context_chars,
        )

