from __future__ import annotations

from ...services import ServiceError
from ...integrations.ai_server.openai_compatible import chat_completion as openai_chat_completion
from .contracts import GenerationResult


class GenerationCloudExecutionMixin:
    def _generate_nvidia(
        self,
        request_id: str,
        requested_model_id: str,
        used_model_id: str,
        messages: list[dict[str, str]],
        *,
        model_name: str | None = None,
    ) -> GenerationResult:
        if not self.settings.ai_api_key:
            raise ServiceError("NVIDIA 모델을 사용하기 위한 API key가 없습니다.", 503)
        resolved_model_name = model_name or self._preferred_catalog_model(
            self.settings.ai_model, self.nvidia_status().get("models", [])
        )
        if not resolved_model_name:
            raise ServiceError(
                "NVIDIA API에서 사용할 수 있는 모델을 자동 감지하지 못했습니다.", 503
            )
        data = openai_chat_completion(
            self.settings.ai_base_url,
            {
                "model": resolved_model_name,
                "messages": messages,
                "temperature": self.settings.ai_temperature,
                "top_p": 0.7,
                "max_tokens": self.settings.ai_max_tokens,
                "stream": False,
            },
            self.settings.ai_api_key,
            self.settings.request_timeout_seconds,
        )
        return GenerationResult(
            request_id,
            self._extract_answer(data),
            requested_model_id,
            used_model_id,
            "nvidia",
            resolved_model_name,
        )

    def _generate_groq(
        self,
        request_id: str,
        requested_model_id: str,
        used_model_id: str,
        messages: list[dict[str, str]],
        *,
        model_name: str | None = None,
    ) -> GenerationResult:
        groq = self._groq_settings()
        if not groq.enabled:
            raise ServiceError("Groq 모델이 관리자 설정에서 비활성화되어 있습니다.", 503)
        if not self.settings.groq_api_key:
            raise ServiceError("Groq 모델을 사용하기 위한 API key가 없습니다.", 503)
        resolved_model_name = model_name or self._preferred_catalog_model(
            groq.model, self.groq_status().get("models", [])
        )
        if not resolved_model_name:
            raise ServiceError(
                "Groq API에서 사용할 수 있는 모델을 자동 감지하지 못했습니다.", 503
            )
        data = openai_chat_completion(
            groq.base_url,
            {
                "model": resolved_model_name,
                "messages": messages,
                "temperature": self.settings.ai_temperature,
                "max_completion_tokens": self.settings.ai_max_tokens,
                "stream": False,
            },
            self.settings.groq_api_key,
            self.settings.request_timeout_seconds,
        )
        return GenerationResult(
            request_id,
            self._extract_answer(data),
            requested_model_id,
            used_model_id,
            "groq",
            resolved_model_name,
        )
