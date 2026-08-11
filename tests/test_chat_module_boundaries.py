from __future__ import annotations

import importlib


def test_legacy_chat_modules_alias_canonical_implementations() -> None:
    mappings = {
        "backend.chat_intake": "backend.domains.chat.intake",
        "backend.chat_contexts": "backend.domains.chat.contexts",
        "backend.chat_routing": "backend.domains.chat.routing",
        "backend.chat_streaming": "backend.domains.chat.streaming",
        "backend.canonical_context": "backend.domains.chat.canonical_context",
        "backend.chat_progress": "backend.domains.chat.progress",
    }
    for legacy_name, canonical_name in mappings.items():
        legacy = importlib.import_module(legacy_name)
        canonical = importlib.import_module(canonical_name)
        assert legacy is canonical
