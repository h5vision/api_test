from __future__ import annotations

import urllib.parse


class GenerationCatalogIdsMixin:
    @staticmethod
    def _catalog_model_id(provider: str, model_name: str) -> str:
        return f"{provider}:{urllib.parse.quote(model_name, safe='')}"

    @staticmethod
    def _parse_catalog_model_id(
        model_id: str,
        provider: str,
    ) -> str | None:
        prefix = f"{provider}:"
        if not model_id.startswith(prefix):
            return None
        encoded = model_id.removeprefix(prefix).strip()
        return urllib.parse.unquote(encoded) if encoded else None

    @staticmethod
    def _preferred_catalog_model(
        configured_model: str,
        models: list[str],
    ) -> str | None:
        configured = configured_model.strip()
        if configured and configured in models:
            return configured
        return models[0] if models else None
