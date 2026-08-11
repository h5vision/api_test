from __future__ import annotations

from time import monotonic
from typing import Any

from ...integrations.ai_server.ollama import probe_model_catalog as probe_ollama_model_catalog
from ...integrations.ai_server.openai_compatible import probe_model_catalog as probe_openai_model_catalog


class GenerationCatalogStatusMixin:
    def backendai_status(self, *, force: bool = False) -> dict[str, Any]:
        now = monotonic()
        if (
            not force
            and self._status_cache is not None
            and now - self._status_cached_at < 10
        ):
            return dict(self._status_cache)

        with self._status_lock:
            now = monotonic()
            if (
                not force
                and self._status_cache is not None
                and now - self._status_cached_at < 10
            ):
                return dict(self._status_cache)

            status = self._probe_backendai()
            self._status_cache = status
            self._status_cached_at = monotonic()
            return dict(status)

    def _probe_backendai(self) -> dict[str, Any]:
        selected = self._parse_catalog_model_id(self.default_model_id, "backendai")
        return probe_ollama_model_catalog(
            self._backendai_base_url(),
            self.settings.backendai_api_key,
            timeout_seconds=2,
            selected_model=selected,
            preferred_model=self.settings.backendai_model,
        )

    def _backendai_available(self) -> bool:
        return self.backendai_status()["status"] == "online"

    @staticmethod
    def _probe_openai_catalog(
        base_url: str,
        api_key: str,
        *,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        return probe_openai_model_catalog(
            base_url,
            api_key,
            timeout_seconds=timeout_seconds,
        )

    def nvidia_status(self, *, force: bool = False) -> dict[str, Any]:
        now = monotonic()
        if (
            not force
            and self._nvidia_status_cache is not None
            and now - self._nvidia_status_cached_at < 30
        ):
            return dict(self._nvidia_status_cache)

        with self._nvidia_status_lock:
            now = monotonic()
            if (
                not force
                and self._nvidia_status_cache is not None
                and now - self._nvidia_status_cached_at < 30
            ):
                return dict(self._nvidia_status_cache)
            status = self._probe_openai_catalog(
                self.settings.ai_base_url,
                self.settings.ai_api_key,
                timeout_seconds=5,
            )
            self._nvidia_status_cache = status
            self._nvidia_status_cached_at = monotonic()
            return dict(status)

    def groq_status(self, *, force: bool = False) -> dict[str, Any]:
        now = monotonic()
        if (
            not force
            and self._groq_status_cache is not None
            and now - self._groq_status_cached_at < 30
        ):
            return dict(self._groq_status_cache)

        with self._groq_status_lock:
            now = monotonic()
            if (
                not force
                and self._groq_status_cache is not None
                and now - self._groq_status_cached_at < 30
            ):
                return dict(self._groq_status_cache)

            status = self._probe_groq()
            self._groq_status_cache = status
            self._groq_status_cached_at = monotonic()
            return dict(status)

    def _probe_groq(self) -> dict[str, Any]:
        groq = self._groq_settings()
        if not groq.enabled:
            return {
                "status": "offline",
                "connected": False,
                "model_available": False,
                "latency_ms": 0,
                "error": "disabled",
            }
        return self._probe_openai_catalog(
            groq.base_url,
            self.settings.groq_api_key,
            timeout_seconds=5,
        )
