from __future__ import annotations

from ...services import ServiceError
from .contracts import GenerationResult


def generate_cloud_or_custom(
    router,
    *,
    resolved_request_id: str,
    requested: str,
    messages: list[dict[str, str]],
    routing_metadata: dict[str, str | None] | None,
) -> GenerationResult:
    nvidia_model = router._parse_catalog_model_id(requested, "nvidia")
    if requested == router.settings.nvidia_public_model_id:
        nvidia_status = router.nvidia_status()
        nvidia_model = router._preferred_catalog_model(
            router.settings.ai_model,
            nvidia_status.get("models", []),
        )
    if nvidia_model is not None:
        nvidia_models = router.nvidia_status().get("models", [])
        if nvidia_model not in nvidia_models:
            raise ServiceError(
                "NVIDIA API에서 사용할 수 없는 model_id입니다: "
                f"{requested}",
                status_code=422,
            )
        return router._generate_nvidia(
            resolved_request_id,
            requested,
            requested,
            messages,
            model_name=nvidia_model,
        )

    groq_model = router._parse_catalog_model_id(requested, "groq")
    if requested == router.settings.groq_public_model_id:
        groq_status = router.groq_status()
        groq_model = router._preferred_catalog_model(
            router._groq_settings().model,
            groq_status.get("models", []),
        )
    if groq_model is not None:
        groq_models = router.groq_status().get("models", [])
        if groq_model not in groq_models:
            raise ServiceError(
                "Groq API에서 사용할 수 없는 model_id입니다: "
                f"{requested}",
                status_code=422,
            )
        return router._generate_groq(
            resolved_request_id,
            requested,
            requested,
            messages,
            model_name=groq_model,
        )

    if (
        router._custom_provider_registry is not None
        and router._custom_provider_registry.parse_model_id(requested) is not None
    ):
        answer, provider_name, used_model_name = (
            router._custom_provider_registry.generate(
                requested,
                messages,
                routing_metadata=routing_metadata,
            )
        )
        return GenerationResult(
            resolved_request_id,
            answer,
            requested,
            requested,
            provider_name,
            used_model_name,
        )
    raise ServiceError(f"지원하지 않는 model_id입니다: {requested}", status_code=422)
