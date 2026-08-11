from __future__ import annotations

import urllib.parse
from collections.abc import Callable

from ...services import ServiceError
from ...integrations.ai_server.ollama import chat as ollama_chat
from .context import _frontend_attachment_context
from .contracts import GenerationResult


def try_generate_backendai(
    router,
    *,
    resolved_request_id: str,
    requested: str,
    messages: list[dict[str, str]],
    frontend_context,
    routing_metadata: dict[str, str | None] | None,
    delta_callback: Callable[[str], None] | None,
) -> GenerationResult | None:
    backendai_model: str | None = None
    if requested == router.settings.backendai_public_model_id:
        backendai_models = router.backendai_status(force=True).get("models", [])
        backendai_model = router._preferred_catalog_model(
            router.settings.backendai_model,
            backendai_models,
        )
    elif requested.startswith("backendai:"):
        candidate = requested.removeprefix("backendai:").strip()
        available_models = router.backendai_status(force=True).get("models", [])
        if candidate and candidate in available_models:
            backendai_model = candidate
        else:
            raise ServiceError(
                f"BackendAI 서버에서 사용할 수 없는 model_id입니다: {requested}",
                status_code=422,
            )
    if backendai_model is None:
        return None

    _context_text, ollama_images = _frontend_attachment_context(frontend_context)
    backendai_messages = messages
    if ollama_images and messages:
        backendai_messages = [dict(message) for message in messages]
        backendai_messages[-1]["images"] = ollama_images  # type: ignore[assignment]
    try:
        if delta_callback is not None:
            streaming = router.stream_backendai(
                requested,
                backendai_messages,
                request_id=resolved_request_id,
            )
            fragments: list[str] = []
            for fragment in streaming.deltas:
                fragments.append(fragment)
                delta_callback(fragment)
            answer = "".join(fragments).strip()
            if not answer:
                raise ServiceError("Ollama streaming 응답에 텍스트가 없습니다.")
            return GenerationResult(
                streaming.request_id,
                answer,
                streaming.requested_model_id,
                streaming.used_model_id,
                streaming.provider,
                streaming.used_model_name,
            )

        backendai_status = router.backendai_status()
        if not backendai_status.get("connected"):
            raise ServiceError(
                "외부 AI Model Server에 연결할 수 없습니다. "
                "관리자 페이지의 AI Server IP/Port와 서버 상태를 확인하세요.",
                status_code=503,
            )
        available_models = backendai_status.get("models", [])
        if backendai_model not in available_models:
            raise ServiceError(
                "외부 AI Model Server에서 요청 모델을 찾을 수 없습니다: "
                f"{backendai_model}",
                status_code=503,
            )
        data = ollama_chat(
            router._backendai_base_url(),
            {
                "model": backendai_model,
                "messages": backendai_messages,
                "stream": False,
                "options": {
                    "temperature": router.settings.ai_temperature,
                    "num_predict": router.settings.ai_max_tokens,
                    "num_ctx": router.settings.ai_context_window_tokens,
                },
            },
            router.settings.backendai_api_key,
            router.settings.request_timeout_seconds,
            extra_headers={
                f"X-Vision-{key.replace('_', '-').title()}": urllib.parse.quote(
                    str(value), safe="._:/-"
                )
                for key, value in (routing_metadata or {}).items()
                if value is not None and str(value)
            },
        )
        return GenerationResult(
            resolved_request_id,
            router._extract_answer(data),
            requested,
            requested,
            "backendai",
            backendai_model,
        )
    except ServiceError:
        if (
            not router.settings.allow_cloud_fallback
            or not router._model_enabled(router.settings.nvidia_public_model_id)
        ):
            raise
        requested_for_fallback = router.settings.nvidia_public_model_id
        return router._generate_nvidia(
            resolved_request_id,
            requested,
            requested_for_fallback,
            messages,
        )
