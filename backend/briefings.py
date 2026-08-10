from __future__ import annotations

from typing import Any

from .rag_lab import RagLabProjectBinding
from .schemas import ProjectBriefingResponse


def briefing_revision_status(
    requested_commit_id: str | None,
    index_commit: str | None,
    *,
    outdated: bool,
) -> str:
    requested = (requested_commit_id or "").strip().casefold()
    indexed = (index_commit or "").strip().casefold()
    if requested:
        if not indexed:
            return "unknown"
        matches = requested == indexed or (
            min(len(requested), len(indexed)) >= 7
            and (requested.startswith(indexed) or indexed.startswith(requested))
        )
        return "same" if matches else "different"
    return "different" if outdated else "unknown"


def build_project_briefing_response(
    project_id: str,
    commit_id: str | None,
    binding: RagLabProjectBinding,
    value: dict[str, Any],
) -> ProjectBriefingResponse:
    index_commit = str(value.get("index_commit") or value.get("commit") or "").strip() or None
    outdated = bool(value.get("outdated"))
    metadata = {
        key: value[key]
        for key in (
            "materials",
            "truncated",
            "evidence_tokens",
            "briefing_tokens",
            "cited",
        )
        if key in value
    }
    return ProjectBriefingResponse(
        project_id=project_id,
        external_project_id=binding.external_project_id,
        briefing=str(value["briefing"]),
        references=[item for item in value.get("references", []) if isinstance(item, dict)],
        reference_files=[item for item in value.get("reference_files", []) if isinstance(item, dict)],
        mentioned_files=[item for item in value.get("mentioned_files", []) if isinstance(item, dict)],
        structure=dict(value.get("structure") or {}),
        commit=str(value.get("commit") or "").strip() or None,
        index_commit=index_commit,
        requested_commit_id=(commit_id or "").strip() or None,
        revision_status=briefing_revision_status(
            commit_id,
            index_commit,
            outdated=outdated,
        ),
        generated_at=str(value.get("generated_at") or "").strip() or None,
        outdated=outdated,
        ok=bool(value.get("ok", True)),
        metadata=metadata,
    )
