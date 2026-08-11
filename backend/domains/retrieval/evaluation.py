from __future__ import annotations

import re
from dataclasses import dataclass

from .schemas import Source
from .text import classify_index_path, is_low_information_chunk


@dataclass(frozen=True)
class RetrievalEvaluationCase:
    name: str
    query: str
    expected_path_patterns: tuple[str, ...]
    allowed_locales: tuple[str, ...] = ("ko", "en")


@dataclass(frozen=True)
class RetrievalEvaluationResult:
    name: str
    hit_at_k: bool
    precision_at_k: float
    junk_chunk_rate: float
    wrong_language_rate: float
    citation_ready_rate: float


def evaluate_retrieval(
    case: RetrievalEvaluationCase,
    sources: list[Source],
) -> RetrievalEvaluationResult:
    """Calculate deterministic quality gates for a labelled RAG query."""

    if not sources:
        return RetrievalEvaluationResult(case.name, False, 0.0, 0.0, 0.0, 0.0)
    patterns = [re.compile(pattern, re.IGNORECASE) for pattern in case.expected_path_patterns]
    relevant = [
        any(pattern.search(source.path or "") for pattern in patterns)
        for source in sources
    ]
    junk = [is_low_information_chunk(source.text) for source in sources]
    wrong_language: list[bool] = []
    citation_ready: list[bool] = []
    for source in sources:
        profile = classify_index_path(source.path, source.language)
        locale = str(source.metadata.get("locale") or profile.get("locale") or "")
        wrong_language.append(
            bool(profile.get("is_translation"))
            and bool(locale)
            and locale not in case.allowed_locales
        )
        citation_ready.append(
            bool(source.path)
            and source.line_start is not None
            and source.line_end is not None
            and bool(source.document_version_id)
        )
    count = len(sources)
    return RetrievalEvaluationResult(
        name=case.name,
        hit_at_k=any(relevant),
        precision_at_k=round(sum(relevant) / count, 4),
        junk_chunk_rate=round(sum(junk) / count, 4),
        wrong_language_rate=round(sum(wrong_language) / count, 4),
        citation_ready_rate=round(sum(citation_ready) / count, 4),
    )
