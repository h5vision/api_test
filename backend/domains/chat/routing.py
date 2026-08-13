from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .schemas import ChatRequest

_AUTO_PROJECT_IDS = {"", "__auto__", "auto", "default"}
_PATH_RE = re.compile(r"(?:^|[\\/\s])(?:[\w.-]+[\\/])+[\w.-]+|\b[\w.-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|kt|cs|cpp|c|h|hpp|md|yaml|yml|json|toml|sql)\b", re.I)
_PROJECT_PHRASES = (
    "이 프로젝트", "현재 프로젝트", "이 코드", "현재 코드", "이 파일", "현재 파일",
    "this project", "current project", "this code", "current code", "this file",
    "repository", "repo 구조", "codebase",
)


@dataclass(frozen=True)
class ChatRouteDecision:
    route: str
    project_required: bool
    reasons: tuple[str, ...]


def _context_signals(context: Any) -> tuple[bool, list[str]]:
    if not isinstance(context, list):
        return bool(isinstance(context, str) and context.strip()), ["frontend_context"] if isinstance(context, str) and context.strip() else []
    reasons: list[str] = []
    project_signal = False
    for item in context:
        item_id = str(getattr(item, "id", "") or "").lower()
        name = str(getattr(item, "name", "") or "").lower()
        if item_id in {"vscode.workspace", "vscode.reference", "vscode.active_editor", "vscode.selection", "vscode.diagnostics", "vscode.repository"}:
            project_signal = True
            reasons.append(item_id)
            continue
        if item_id == "vscode.command":
            reasons.append("vscode.command")
        if item_id == "vscode.extra":
            value = getattr(item, "value", None)
            text = ""
            if isinstance(value, dict):
                candidate = value.get("content")
                if isinstance(candidate, str):
                    text = candidate
            try:
                payload = json.loads(text) if text else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            future = payload.get("future_fields") if isinstance(payload, dict) else None
            if isinstance(future, dict) and any(
                key in future
                for key in ("active_editor", "selection", "diagnostics", "repository", "workspace_folders", "files")
            ):
                project_signal = True
                reasons.append("vscode.extra.project_context")
        if "workspace" in name or "file" in name or "code" in name:
            project_signal = True
            reasons.append("context_name")
    return project_signal, reasons


def classify_chat_request(payload: ChatRequest) -> ChatRouteDecision:
    """Conservatively decide whether Vision project grounding is required.

    This is intentionally deterministic. The Frontend can stay thin, while Vision avoids
    sending ordinary conversation through project resolution/RAG. Project-like words by
    themselves are not enough: P3-C receives project/Snapshot identity through a separate
    Context contract, and an absent Context must keep ordinary Chat available.
    """

    reasons: list[str] = []
    normalized_project_id = (payload.project_id or "").strip().lower()
    if normalized_project_id not in _AUTO_PROJECT_IDS:
        reasons.append("explicit_project_id")

    context_project, context_reasons = _context_signals(payload.context)
    reasons.extend(context_reasons)

    message = payload.message.strip()
    lowered = message.lower()
    lexical_project = any(phrase in lowered for phrase in _PROJECT_PHRASES) or bool(_PATH_RE.search(message))
    if lexical_project:
        reasons.append("project_language_without_identity")

    project_required = bool(
        normalized_project_id not in _AUTO_PROJECT_IDS
        or payload.snapshot_id
        or context_project
    )
    return ChatRouteDecision(
        route="project_grounded" if project_required else "general",
        project_required=project_required,
        reasons=tuple(dict.fromkeys(reasons)),
    )


def allows_unresolved_project_fallback(
    decision: ChatRouteDecision,
    *,
    resolved_project_id: str | None,
    snapshot_id: str | None,
) -> bool:
    """Keep Chat available when a Frontend workspace hint is not indexed.

    A workspace-derived ``project_id`` is advisory until Vision resolves it to
    one indexed project.  An explicit Snapshot remains strict because silently
    discarding immutable grounding would answer against the wrong revision.
    """

    return bool(
        decision.project_required
        and not resolved_project_id
        and not (snapshot_id or "").strip()
    )
