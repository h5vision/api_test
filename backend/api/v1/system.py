from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Response

from ...contracts.system import LanguageDetectRequest


def create_system_router(
    *,
    settings: Any,
    runtime_settings_resolver: Any,
    vector_store: Any,
    metadata_store: Any,
    project_store: Any,
    vector_store_error: type[Exception],
    language_registry_factory: Callable[[], Any],
) -> APIRouter:
    """Build public health and language-registry endpoints from injected runtime services."""

    router = APIRouter()

    @router.get("/v1/health", tags=["System"])
    def health() -> dict[str, Any]:
        runtime_setup = runtime_settings_resolver.setup_state(refresh=False)
        vector_status: dict[str, Any]
        if runtime_setup.configured:
            try:
                vector_status = vector_store.stats()
            except vector_store_error as exc:
                vector_status = {"status": "unavailable", "error": type(exc).__name__}
        else:
            vector_status = {
                "status": "setup_required",
                "missing": list(runtime_setup.missing),
            }
        return {
            "status": "ok",
            "service": "vs-code-ai-assistant-backend",
            "version": "3.0.0",
            "instance_id": settings.instance_id,
            "configuration": settings.public_status(),
            "runtime_setup": {
                "configured": runtime_setup.configured,
                "missing": list(runtime_setup.missing),
                "errors": list(runtime_setup.errors),
            },
            "vector_store": vector_status,
            "metadata_store": metadata_store.status(),
            "project_store": project_store.status(),
            "message": "백엔드 API 서버에서 응답중 입니다.",
        }

    @router.get(
        "/v1/languages",
        tags=["System"],
        summary="List the VS Code-compatible language registry",
    )
    def list_languages(response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "public, max-age=3600"
        return language_registry_factory().catalog()

    @router.post(
        "/v1/languages/detect",
        tags=["System"],
        summary="Detect or normalize one VS Code document language",
    )
    def detect_language(payload: LanguageDetectRequest) -> dict[str, Any]:
        return language_registry_factory().detect(
            explicit_language_id=payload.language_id,
            file_name=payload.file_name,
            path=payload.path,
            content=payload.content,
            workspace_languages=payload.workspace_languages,
            session_languages=payload.session_languages,
            workspace_history_languages=payload.workspace_history_languages,
            global_history_languages=payload.global_history_languages,
        ).public_dict()

    return router
