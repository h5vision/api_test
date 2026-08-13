from __future__ import annotations

import urllib.parse
from typing import Any
from uuid import uuid4

from ...services import ServiceError
from ...integrations.ai_server.ollama import stream_chat as stream_ollama_chat
from .contracts import StreamingGeneration


class GenerationBackendAIExecutionMixin:
    def stream_backendai(
        self,
        requested_model_id: str | None,
        messages: list[dict[str, str]],
        *,
        request_id: str | None = None,
        routing_metadata: dict[str, str | None] | None = None,
        vision_context: dict[str, Any] | None = None,
    ) -> StreamingGeneration:
        """Open the BackendAI Ollama NDJSON stream without buffering tokens."""
        resolved_request_id = request_id or f"req_{uuid4().hex}"
        requested = requested_model_id or self.default_model_id
        if not self._model_enabled(requested):
            raise ServiceError(
                f"관리자가 API 사용을 비활성화한 model_id입니다: {requested}",
                status_code=403,
            )

        backendai_model: str | None = None
        if requested == self.settings.backendai_public_model_id:
            backendai_models = self.backendai_status(force=True).get("models", [])
            backendai_model = self._preferred_catalog_model(
                self.settings.backendai_model,
                backendai_models,
            )
        elif requested.startswith("backendai:"):
            candidate = requested.removeprefix("backendai:").strip()
            available_models = self.backendai_status(force=True).get("models", [])
            if candidate and candidate in available_models:
                backendai_model = candidate
        if not backendai_model:
            raise ServiceError(
                f"선택한 모델은 Ollama 토큰 스트리밍 대상이 아닙니다: {requested}",
                status_code=422,
            )

        backendai_status = self.backendai_status()
        if not backendai_status.get("connected"):
            raise ServiceError("외부 AI Model Server에 연결할 수 없습니다.", 503)
        if backendai_model not in backendai_status.get("models", []):
            raise ServiceError(
                f"외부 AI Model Server에서 요청 모델을 찾을 수 없습니다: {backendai_model}",
                503,
            )

        base_url = self._backendai_base_url()
        payload: dict[str, Any] = {
            "model": backendai_model,
            "messages": messages,
            "stream": True,
            "keep_alive": "30m",
            "options": {
                "temperature": self.settings.ai_temperature,
                "num_predict": self.settings.ai_max_tokens,
                "num_ctx": self.settings.ai_context_window_tokens,
            },
        }
        if vision_context:
            payload["vision_context"] = vision_context
        extra_headers = {
            f"X-Vision-{key.replace('_', '-').title()}": urllib.parse.quote(
                str(value), safe="._:/-"
            )
            for key, value in (routing_metadata or {}).items()
            if value is not None and str(value)
        }
        return StreamingGeneration(
            request_id=resolved_request_id,
            requested_model_id=requested,
            used_model_id=requested,
            provider="backendai",
            used_model_name=backendai_model,
            inference_protocol="ollama",
            inference_endpoint=self._endpoint_label(base_url),
            deltas=stream_ollama_chat(
                base_url,
                payload,
                self.settings.backendai_api_key,
                self.settings.request_timeout_seconds,
                extra_headers=extra_headers,
            ),
        )
