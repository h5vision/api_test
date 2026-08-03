from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping
from urllib.parse import unquote, urlsplit


_SEARCHABLE_STATUSES = {
    "completed",
    "ready",
    "partially_completed",
    "partially_ready",
    "stale",
}
_FUZZY_MIN_SCORE = 0.78
_FUZZY_MIN_MARGIN = 0.08


@dataclass(frozen=True)
class ProjectResolution:
    requested_project_id: str
    resolved_project_id: str | None
    strategy: str
    confidence: float
    candidates: tuple[str, ...] = ()

    def metadata(self) -> dict[str, Any]:
        return {
            "requested_project_id": self.requested_project_id,
            "resolved_project_id": self.resolved_project_id,
            "strategy": self.strategy,
            "confidence": round(self.confidence, 4),
            "candidates": list(self.candidates),
        }


@dataclass(frozen=True)
class _Candidate:
    project_id: str
    display_name: str
    searchable: bool
    aliases: tuple[str, ...]


def parse_project_id_aliases(raw: str) -> tuple[tuple[str, str], ...]:
    """Parse `alias=canonical` pairs separated by comma, semicolon, or newline."""

    pairs: list[tuple[str, str]] = []
    for item in re.split(r"[,;\n]+", raw):
        alias, separator, canonical = item.partition("=")
        if not separator:
            continue
        alias = alias.strip()
        canonical = canonical.strip()
        if alias and canonical:
            pairs.append((alias, canonical))
    return tuple(pairs)


def resolve_project_id(
    requested_project_id: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    configured_aliases: Iterable[tuple[str, str]] = (),
) -> ProjectResolution:
    """Resolve a frontend identifier to one canonical indexed project ID.

    Resolution is deterministic and project-scoped. It never merges sources
    from multiple projects. A low-confidence or ambiguous match stays
    unresolved so the API can return a useful conflict response.
    """

    requested = requested_project_id.strip()
    candidates = _build_candidates(rows)
    if not candidates:
        return ProjectResolution(
            requested_project_id=requested,
            resolved_project_id=None,
            strategy="no_indexed_projects",
            confidence=0.0,
        )

    exact = [
        candidate
        for candidate in candidates
        if requested == candidate.project_id
    ]
    if exact:
        return _resolved(requested, exact[0], "exact_project_id", 1.0)

    requested_reference = _reference_key(requested)
    normalized = [
        candidate
        for candidate in candidates
        if requested_reference == _reference_key(candidate.project_id)
    ]
    if len(normalized) == 1:
        return _resolved(
            requested,
            normalized[0],
            "normalized_project_id",
            1.0,
        )

    explicit_target = _configured_alias_target(
        requested,
        configured_aliases,
    )
    if explicit_target:
        target_reference = _reference_key(explicit_target)
        explicit = [
            candidate
            for candidate in candidates
            if target_reference == _reference_key(candidate.project_id)
        ]
        if len(explicit) == 1:
            return _resolved(
                requested,
                explicit[0],
                "configured_alias",
                0.99,
            )

    requested_keys = _comparison_keys(requested)
    alias_matches = [
        candidate
        for candidate in candidates
        if requested_keys.intersection(candidate.aliases)
    ]
    if len(alias_matches) == 1:
        return _resolved(
            requested,
            alias_matches[0],
            "project_alias",
            0.98,
        )
    if len(alias_matches) > 1:
        return ProjectResolution(
            requested_project_id=requested,
            resolved_project_id=None,
            strategy="ambiguous_project_alias",
            confidence=0.0,
            candidates=tuple(
                candidate.project_id for candidate in alias_matches
            ),
        )

    searchable = [candidate for candidate in candidates if candidate.searchable]
    fuzzy_pool = searchable or candidates
    fuzzy_scores = sorted(
        (
            (_best_fuzzy_score(requested, candidate), candidate)
            for candidate in fuzzy_pool
        ),
        key=lambda item: (-item[0], item[1].project_id.casefold()),
    )
    best_score, best_candidate = fuzzy_scores[0]
    second_score = fuzzy_scores[1][0] if len(fuzzy_scores) > 1 else 0.0
    if (
        best_score >= _FUZZY_MIN_SCORE
        and best_score - second_score >= _FUZZY_MIN_MARGIN
    ):
        return _resolved(
            requested,
            best_candidate,
            "fuzzy_project_alias",
            best_score,
        )

    non_default_searchable = [
        candidate
        for candidate in searchable
        if candidate.project_id.casefold() != "default"
    ]
    if len(non_default_searchable) == 1:
        return _resolved(
            requested,
            non_default_searchable[0],
            "sole_indexed_project",
            0.5,
        )

    suggestions = tuple(
        candidate.project_id
        for score, candidate in fuzzy_scores[:3]
        if score > 0.0
    )
    return ProjectResolution(
        requested_project_id=requested,
        resolved_project_id=None,
        strategy="unresolved_project_id",
        confidence=best_score,
        candidates=suggestions,
    )


def _build_candidates(
    rows: Iterable[Mapping[str, Any]],
) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for row in rows:
        project_id = str(row.get("project_id") or "").strip()
        if not project_id:
            continue
        display_name = str(
            row.get("display_name") or project_id.rsplit("/", 1)[-1]
        ).strip()
        status = str(row.get("index_status") or "").strip().lower()
        searchable = bool(row.get("current_snapshot_id")) or (
            status in _SEARCHABLE_STATUSES
        )
        alias_values = {
            project_id,
            display_name,
            project_id.rsplit("/", 1)[-1],
        }
        aliases: set[str] = set()
        for value in alias_values:
            aliases.update(_comparison_keys(value))
        candidates.append(
            _Candidate(
                project_id=project_id,
                display_name=display_name,
                searchable=searchable,
                aliases=tuple(sorted(aliases)),
            )
        )
    return candidates


def _configured_alias_target(
    requested: str,
    aliases: Iterable[tuple[str, str]],
) -> str | None:
    requested_keys = _comparison_keys(requested)
    for alias, canonical in aliases:
        if requested_keys.intersection(_comparison_keys(alias)):
            return canonical
    return None


def _resolved(
    requested: str,
    candidate: _Candidate,
    strategy: str,
    confidence: float,
) -> ProjectResolution:
    return ProjectResolution(
        requested_project_id=requested,
        resolved_project_id=candidate.project_id,
        strategy=strategy,
        confidence=confidence,
        candidates=(candidate.project_id,),
    )


def _best_fuzzy_score(requested: str, candidate: _Candidate) -> float:
    requested_compact = _compact_key(requested)
    if len(requested_compact) < 4:
        return 0.0
    scores = [
        SequenceMatcher(None, requested_compact, _compact_key(alias)).ratio()
        for alias in candidate.aliases
        if len(_compact_key(alias)) >= 4
    ]
    return max(scores, default=0.0)


def _comparison_keys(value: str) -> set[str]:
    reference = _reference_key(value)
    basename = reference.rsplit("/", 1)[-1]
    return {
        key
        for key in (
            reference.casefold(),
            basename.casefold(),
            _compact_key(reference),
            _compact_key(basename),
        )
        if key
    }


def _reference_key(value: str) -> str:
    raw = unquote(value.strip()).replace("\\", "/")
    if "://" in raw:
        parsed = urlsplit(raw)
        raw = parsed.path
    else:
        ssh_match = re.match(r"^[^@\s]+@[^:\s]+:(.+)$", raw)
        if ssh_match:
            raw = ssh_match.group(1)
    raw = raw.split("?", 1)[0].split("#", 1)[0].strip("/")
    if raw.casefold().startswith("github.com/"):
        raw = raw[len("github.com/") :]
    if raw.casefold().endswith(".git"):
        raw = raw[:-4]
    return re.sub(r"/+", "/", raw).casefold()


def _compact_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())
