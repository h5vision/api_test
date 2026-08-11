from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from ...ai_providers import AIProviderRegistry
from ...config import Settings
from ...runtime_services import RuntimeGroqSettings
from ...schemas import ChatContextItem, HistoryMessage, Source
from ...services import ServiceError
from .catalog import GenerationCatalogMixin
from .context import passthrough_messages
from .contracts import GenerationResult
from .execution import GenerationExecutionMixin
from .orchestration_backendai import try_generate_backendai
from .orchestration_cloud import generate_cloud_or_custom


class GenerationRouter(GenerationExecutionMixin, GenerationCatalogMixin):
    def __init__(
        self,
        settings: Settings,
        backendai_base_url_provider: Callable[[], str] | None = None,
        groq_settings_provider: Callable[[], RuntimeGroqSettings] | None = None,
        model_enabled_provider: Callable[[str], bool] | None = None,
        custom_provider_registry: AIProviderRegistry | None = None,
    ) -> None:
        self.settings = settings
        self._backendai_base_url_provider = backendai_base_url_provider
        self._groq_settings_provider = groq_settings_provider
        self._model_enabled_provider = model_enabled_provider
        self._custom_provider_registry = custom_provider_registry
        self._status_lock = threading.Lock()
        self._status_cached_at = 0.0
        self._status_cache: dict[str, Any] | None = None
        self._nvidia_status_lock = threading.Lock()
        self._nvidia_status_cached_at = 0.0
        self._nvidia_status_cache: dict[str, Any] | None = None
        self._groq_status_lock = threading.Lock()
        self._groq_status_cached_at = 0.0
        self._groq_status_cache: dict[str, Any] | None = None


    def generate(
        self,
        requested_model_id: str | None,
        question: str,
        sources: list[Source],
        history: list[HistoryMessage],
        frontend_context: str | list[ChatContextItem],
        project_id: str,
        session_id: str,
        *,
        request_id: str | None = None,
        prompt_mode: str = "passthrough",
        routing_metadata: dict[str, str | None] | None = None,
        messages_override: list[dict[str, str]] | None = None,
        delta_callback: Callable[[str], None] | None = None,
    ) -> GenerationResult:
        resolved_request_id = request_id or f"req_{uuid4().hex}"
        requested = requested_model_id or self.default_model_id
        if not self._model_enabled(requested):
            raise ServiceError(
                f"관리자가 API 사용을 비활성화한 model_id입니다: {requested}",
                status_code=403,
            )
        if messages_override is not None:
            messages = self._validate_external_messages(messages_override)
        elif prompt_mode in {"passthrough", "direct", "provider_managed"}:
            messages = passthrough_messages(question, history, frontend_context)
        else:
            raise ServiceError(
                f"지원하지 않는 prompt_mode입니다: {prompt_mode}", status_code=500
            )

        backendai_result = try_generate_backendai(
            self,
            resolved_request_id=resolved_request_id,
            requested=requested,
            messages=messages,
            frontend_context=frontend_context,
            routing_metadata=routing_metadata,
            delta_callback=delta_callback,
        )
        if backendai_result is not None:
            return backendai_result
        return generate_cloud_or_custom(
            self,
            resolved_request_id=resolved_request_id,
            requested=requested,
            messages=messages,
            routing_metadata=routing_metadata,
        )
