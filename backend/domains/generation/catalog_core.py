from __future__ import annotations

import urllib.parse

from ...ai_providers import AIProviderStoreError
from ...runtime_services import RuntimeGroqSettings


class GenerationCatalogCoreMixin:
    def _backendai_base_url(self) -> str:
        if self._backendai_base_url_provider is None:
            return self.settings.backendai_base_url
        return self._backendai_base_url_provider().rstrip("/")

    def _groq_settings(self) -> RuntimeGroqSettings:
        if self._groq_settings_provider is not None:
            return self._groq_settings_provider()
        return RuntimeGroqSettings(
            enabled=bool(self.settings.groq_api_key),
            base_url=self.settings.groq_base_url,
            model=self.settings.groq_model,
            default_model_id=self.settings.default_model_id,
        )

    @property
    def default_model_id(self) -> str:
        return self._groq_settings().default_model_id

    def chat_processing_mode(self, model_id: str | None = None) -> str:
        requested = model_id or self.default_model_id
        if self._custom_provider_registry is None:
            return "vision_managed"
        try:
            return self._custom_provider_registry.chat_processing_mode(requested)
        except AIProviderStoreError:
            return "vision_managed"

    def _model_enabled(self, model_id: str) -> bool:
        if self._model_enabled_provider is None:
            return True
        return bool(self._model_enabled_provider(model_id))

    @staticmethod
    def _endpoint_label(base_url: str) -> str | None:
        parsed = urllib.parse.urlparse(base_url)
        if not parsed.hostname:
            return None
        if parsed.port is not None:
            return f"{parsed.hostname}:{parsed.port}"
        return parsed.hostname

    def invalidate_backendai_status(self) -> None:
        with self._status_lock:
            self._status_cache = None
            self._status_cached_at = 0.0

    def invalidate_groq_status(self) -> None:
        with self._groq_status_lock:
            self._groq_status_cache = None
            self._groq_status_cached_at = 0.0

    def invalidate_nvidia_status(self) -> None:
        with self._nvidia_status_lock:
            self._nvidia_status_cache = None
            self._nvidia_status_cached_at = 0.0
