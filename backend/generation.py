from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from time import monotonic, perf_counter
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from .ai_providers import AIProviderRegistry, AIProviderStoreError
from .config import Settings
from .runtime_services import RuntimeGroqSettings
from .schemas import ChatContextItem, HistoryMessage, ModelInfo, Source
from .services import ChatService, ServiceError, _post_json


@dataclass(frozen=True)
class GenerationResult:
    request_id: str
    answer: str
    requested_model_id: str
    used_model_id: str
    provider: str
    used_model_name: str


class PromptAssembler:
    _MAX_FRONTEND_CONTEXT_CHARS = 200_000

    @classmethod
    def _frontend_context(
        cls,
        items: str | list[ChatContextItem],
    ) -> str:
        if isinstance(items, str):
            normalized = items.strip()
            if not normalized:
                return "사용 가능한 Frontend 첨부 본문이 없습니다."
            clipped = normalized[: cls._MAX_FRONTEND_CONTEXT_CHARS]
            if len(clipped) < len(normalized):
                clipped += "\n[Frontend context가 Backend Prompt 한도로 잘렸습니다.]"
            return clipped

        parts: list[str] = []
        remaining = cls._MAX_FRONTEND_CONTEXT_CHARS
        for item in items:
            item_id = (item.id or "").strip()
            name = (item.name or "").strip()
            if item_id == "vscode.customizations.index" or name.startswith("prompt:"):
                # VS Code/Copilot customization and tool definitions are client
                # internals, not user document content.
                continue

            label = name or item_id or "이름 없는 첨부"
            content: str | None = None
            if isinstance(item.value, str):
                content = item.value
            elif isinstance(item.value, dict):
                for key in ("content", "text", "selection", "code"):
                    candidate = item.value.get(key)
                    if isinstance(candidate, str) and candidate.strip():
                        content = candidate
                        break

            prefix = f"[첨부 {len(parts) + 1}] {label}"
            if content is None:
                part = (
                    f"{prefix}\n"
                    "파일 참조만 전달되었으며 파일 본문은 전달되지 않았습니다."
                )
            else:
                normalized = content.strip()
                if not normalized:
                    part = f"{prefix}\n파일 본문이 비어 있습니다."
                else:
                    available = max(0, remaining - len(prefix) - 1)
                    clipped = normalized[:available]
                    part = f"{prefix}\n{clipped}"
                    if len(clipped) < len(normalized):
                        part += "\n[첨부 내용이 Backend Prompt 한도로 잘렸습니다.]"

            if len(part) > remaining:
                break
            parts.append(part)
            remaining -= len(part)
            if remaining <= 0:
                break

        return "\n\n".join(parts) or "사용 가능한 Frontend 첨부 본문이 없습니다."

    @staticmethod
    def build(
        question: str,
        sources: list[Source],
        history: list[HistoryMessage],
        frontend_context: str | list[ChatContextItem],
    ) -> list[dict[str, str]]:
        context_parts: list[str] = []
        for index, source in enumerate(sources, start=1):
            label = source.path or source.document_id
            lines = ""
            if source.line_start is not None:
                lines = f" lines {source.line_start}-{source.line_end or source.line_start}"
            context_parts.append(f"[{index}] {label}{lines}\n{source.text}")
        context = "\n\n".join(context_parts) or "검색된 프로젝트 문서가 없습니다."
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "제공된 근거만 사용해 한국어로 답한다. 근거에 없는 파일·동작은 추측하지 않는다.\n"
                    "각 주장 끝에 사용한 근거 번호를 [1], [2] 형식으로 표기한다.\n"
                    "근거가 질문에 답하기 부족하면 다른 설명 없이 정확히 다음 한 줄만 출력한다: NO_EVIDENCE"
                ),
            }
        ]
        messages.extend(
            {"role": item.role, "content": item.content} for item in history[-10:]
        )
        messages.append(
            {
                "role": "user",
                "content": (
                    f"프로젝트 검색 결과:\n{context}\n\n"
                    "Frontend 첨부 컨텍스트:\n"
                    f"{PromptAssembler._frontend_context(frontend_context)}\n\n"
                    f"질문:\n{question}"
                ),
            }
        )
        return messages


class GenerationRouter:
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
        self._legacy_local = ChatService(settings)
        self._status_lock = threading.Lock()
        self._status_cached_at = 0.0
        self._status_cache: dict[str, Any] | None = None
        self._groq_status_lock = threading.Lock()
        self._groq_status_cached_at = 0.0
        self._groq_status_cache: dict[str, Any] | None = None

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
        headers = {"Accept": "application/json"}
        if self.settings.backendai_api_key:
            headers["Authorization"] = f"Bearer {self.settings.backendai_api_key}"
        request = urllib.request.Request(
            f"{self._backendai_base_url()}/api/tags",
            headers=headers,
            method="GET",
        )
        started_at = perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                if not 200 <= response.status < 300:
                    return {
                        "status": "offline",
                        "connected": False,
                        "model_available": False,
                        "latency_ms": round((perf_counter() - started_at) * 1000),
                        "error": f"http_{response.status}",
                    }
                data = json.loads(response.read().decode("utf-8"))
                models = data.get("models", [])
                model_names: list[str] = []
                skipped_models: list[str] = []
                for model in models:
                    if not isinstance(model, dict):
                        continue
                    model_name = str(
                        model.get("name") or model.get("model") or ""
                    ).strip()
                    if not model_name:
                        continue
                    capabilities = model.get("capabilities")
                    if (
                        isinstance(capabilities, list)
                        and capabilities
                        and "completion" not in {
                            str(capability).strip().lower()
                            for capability in capabilities
                        }
                    ):
                        skipped_models.append(model_name)
                        continue
                    model_names.append(model_name)
                model_names = sorted(set(model_names))
                model_available = any(
                    self.settings.backendai_model == model_name
                    for model_name in model_names
                )
                return {
                    "status": "online" if model_available else "degraded",
                    "connected": True,
                    "model_available": model_available,
                    "models": model_names,
                    "skipped_non_chat_models": sorted(set(skipped_models)),
                    "latency_ms": round((perf_counter() - started_at) * 1000),
                    "error": None if model_available else "model_not_found",
                }
        except (
            urllib.error.URLError,
            TimeoutError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            reason = "timeout" if isinstance(exc, TimeoutError) else "unreachable"
            if isinstance(exc, urllib.error.URLError) and isinstance(
                exc.reason, TimeoutError
            ):
                reason = "timeout"
            return {
                "status": "offline",
                "connected": False,
                "model_available": False,
                "models": [],
                "latency_ms": round((perf_counter() - started_at) * 1000),
                "error": reason,
            }

    def _backendai_available(self) -> bool:
        return self.backendai_status()["status"] == "online"

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
        if not self.settings.groq_api_key:
            return {
                "status": "offline",
                "connected": False,
                "model_available": False,
                "latency_ms": 0,
                "error": "missing_api_key",
            }
        encoded_model = urllib.parse.quote(groq.model, safe="")
        request = urllib.request.Request(
            f"{groq.base_url}/models/{encoded_model}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.settings.groq_api_key}",
                "User-Agent": "VisionBackend/1.0",
            },
            method="GET",
        )
        started_at = perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                data = json.loads(response.read().decode("utf-8"))
                model_available = (
                    200 <= response.status < 300
                    and str(data.get("id") or "").strip()
                    == groq.model
                )
                return {
                    "status": "online" if model_available else "degraded",
                    "connected": True,
                    "model_available": model_available,
                    "latency_ms": round((perf_counter() - started_at) * 1000),
                    "error": None if model_available else "model_not_found",
                }
        except urllib.error.HTTPError as exc:
            return {
                "status": "offline",
                "connected": False,
                "model_available": False,
                "latency_ms": round((perf_counter() - started_at) * 1000),
                "error": f"http_{exc.code}",
            }
        except (
            urllib.error.URLError,
            TimeoutError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            reason = "timeout" if isinstance(exc, TimeoutError) else "unreachable"
            if isinstance(exc, urllib.error.URLError) and isinstance(
                exc.reason, TimeoutError
            ):
                reason = "timeout"
            return {
                "status": "offline",
                "connected": False,
                "model_available": False,
                "latency_ms": round((perf_counter() - started_at) * 1000),
                "error": reason,
            }

    def models(self) -> list[ModelInfo]:
        backendai_status = self.backendai_status()
        groq_status = self.groq_status()
        groq = self._groq_settings()
        default_model_id = groq.default_model_id
        backendai_models = backendai_status.get("models", [])
        parsed_backendai_url = urllib.parse.urlparse(self._backendai_base_url())
        backendai_location = (
            "local"
            if (parsed_backendai_url.hostname or "").lower()
            in {"127.0.0.1", "localhost", "::1"}
            else "internal"
        )
        backendai_deployment = (
            "local" if backendai_location == "local" else "remote_server"
        )
        backendai_endpoint = self._endpoint_label(self._backendai_base_url())
        nvidia_endpoint = self._endpoint_label(self.settings.ai_base_url)
        groq_endpoint = self._endpoint_label(groq.base_url)
        models = [
            ModelInfo(
                model_id=self.settings.backendai_public_model_id,
                model_name=self.settings.backendai_model,
                display_name=f"AI Model ({self.settings.backendai_model})",
                provider="backendai",
                location=backendai_location,
                deployment_type=backendai_deployment,
                endpoint=backendai_endpoint,
                enabled=self._model_enabled(
                    self.settings.backendai_public_model_id
                ),
                available=bool(backendai_status.get("model_available")),
                is_default=(
                    default_model_id
                    == self.settings.backendai_public_model_id
                ),
                streaming=False,
            ),
            ModelInfo(
                model_id=self.settings.nvidia_public_model_id,
                model_name=self.settings.ai_model,
                display_name=f"NVIDIA Cloud ({self.settings.ai_model})",
                provider="nvidia",
                location="cloud",
                deployment_type="cloud",
                endpoint=nvidia_endpoint,
                enabled=self._model_enabled(
                    self.settings.nvidia_public_model_id
                ),
                available=bool(self.settings.ai_api_key),
                is_default=(
                    default_model_id == self.settings.nvidia_public_model_id
                ),
                streaming=False,
            ),
            ModelInfo(
                model_id=self.settings.groq_public_model_id,
                model_name=groq.model,
                display_name=f"Groq Cloud ({groq.model})",
                provider="groq",
                location="cloud",
                deployment_type="cloud",
                endpoint=groq_endpoint,
                enabled=self._model_enabled(
                    self.settings.groq_public_model_id
                ),
                available=bool(groq_status.get("model_available")),
                is_default=(
                    default_model_id
                    == self.settings.groq_public_model_id
                ),
                streaming=False,
            ),
        ]
        for model_name in backendai_models:
            if model_name == self.settings.backendai_model:
                continue
            model_id = f"backendai:{model_name}"
            models.append(
                ModelInfo(
                    model_id=model_id,
                    model_name=model_name,
                    display_name=f"AI Model ({model_name})",
                    provider="backendai",
                    location=backendai_location,
                    deployment_type=backendai_deployment,
                    endpoint=backendai_endpoint,
                    enabled=self._model_enabled(model_id),
                    available=True,
                    is_default=default_model_id == model_id,
                    streaming=False,
                )
            )
        if self.settings.ai_provider == "local":
            models.append(
                ModelInfo(
                    model_id="local-test",
                    model_name="local-deterministic-test",
                    display_name="Local deterministic test model",
                    provider="local",
                    location="local",
                    deployment_type="local",
                    endpoint="FastAPI process",
                    enabled=self._model_enabled("local-test"),
                    available=True,
                    is_default=default_model_id == "local-test",
                    streaming=False,
                )
            )
        if self._custom_provider_registry is not None:
            try:
                models.extend(
                    self._custom_provider_registry.models(default_model_id)
                )
            except AIProviderStoreError:
                # Built-in providers remain usable even when the optional
                # administrator provider registry cannot reach PostgreSQL.
                pass
        return models

    @staticmethod
    def _extract_answer(data: dict[str, Any]) -> str:
        direct = data.get("answer")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        ollama_message = data.get("message")
        if isinstance(ollama_message, dict):
            ollama_answer = ollama_message.get("content")
            if isinstance(ollama_answer, str) and ollama_answer.strip():
                return ollama_answer.strip()
        try:
            answer = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ServiceError("생성 모델 응답에 answer가 없습니다.") from exc
        if not isinstance(answer, str) or not answer.strip():
            raise ServiceError("생성 모델이 빈 답변을 반환했습니다.")
        return answer.strip()

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
    ) -> GenerationResult:
        resolved_request_id = request_id or f"req_{uuid4().hex}"
        if self.settings.ai_provider == "local" and requested_model_id is None:
            requested = "local-test"
        else:
            requested = requested_model_id or self.default_model_id
        if not self._model_enabled(requested):
            raise ServiceError(
                f"관리자가 API 사용을 비활성화한 model_id입니다: {requested}",
                status_code=403,
            )
        messages = PromptAssembler.build(
            question,
            sources,
            history,
            frontend_context,
        )
        if requested == "local-test":
            answer, _ = self._legacy_local.answer(question, sources, history)
            return GenerationResult(
                resolved_request_id,
                answer,
                requested,
                requested,
                "local",
                "local-deterministic-test",
            )
        backendai_model: str | None = None
        if requested == self.settings.backendai_public_model_id:
            backendai_model = self.settings.backendai_model
        elif requested.startswith("backendai:"):
            candidate = requested.removeprefix("backendai:").strip()
            available_models = self.backendai_status(force=True).get("models", [])
            if candidate and candidate in available_models:
                backendai_model = candidate
            else:
                raise ServiceError(
                    f"BackendAI 서버에서 사용할 수 없는 model_id입니다: {requested}",
                    status_code=422,
                )
        if backendai_model is not None:
            try:
                backendai_status = self.backendai_status()
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
                data = _post_json(
                    f"{self._backendai_base_url()}/api/chat",
                    {
                        "model": backendai_model,
                        "messages": messages,
                        "stream": False,
                        "options": {
                            "temperature": self.settings.ai_temperature,
                            "num_predict": self.settings.ai_max_tokens,
                        },
                    },
                    self.settings.backendai_api_key,
                    self.settings.request_timeout_seconds,
                )
                return GenerationResult(
                    resolved_request_id,
                    self._extract_answer(data),
                    requested,
                    requested,
                    "backendai",
                    backendai_model,
                )
            except ServiceError:
                if (
                    not self.settings.allow_cloud_fallback
                    or not self._model_enabled(
                        self.settings.nvidia_public_model_id
                    )
                ):
                    raise
                requested_for_fallback = self.settings.nvidia_public_model_id
                return self._generate_nvidia(
                    resolved_request_id, requested, requested_for_fallback, messages
                )
        if requested == self.settings.nvidia_public_model_id:
            return self._generate_nvidia(
                resolved_request_id, requested, requested, messages
            )
        if requested == self.settings.groq_public_model_id:
            return self._generate_groq(
                resolved_request_id, requested, requested, messages
            )
        if (
            self._custom_provider_registry is not None
            and self._custom_provider_registry.parse_model_id(requested) is not None
        ):
            answer, provider_name, used_model_name = (
                self._custom_provider_registry.generate(requested, messages)
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

    def _generate_nvidia(
        self,
        request_id: str,
        requested_model_id: str,
        used_model_id: str,
        messages: list[dict[str, str]],
    ) -> GenerationResult:
        if not self.settings.ai_api_key:
            raise ServiceError("NVIDIA 모델을 사용하기 위한 API key가 없습니다.", 503)
        data = _post_json(
            f"{self.settings.ai_base_url}/chat/completions",
            {
                "model": self.settings.ai_model,
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
            self.settings.ai_model,
        )

    def _generate_groq(
        self,
        request_id: str,
        requested_model_id: str,
        used_model_id: str,
        messages: list[dict[str, str]],
    ) -> GenerationResult:
        groq = self._groq_settings()
        if not groq.enabled:
            raise ServiceError("Groq 모델이 관리자 설정에서 비활성화되어 있습니다.", 503)
        if not self.settings.groq_api_key:
            raise ServiceError("Groq 모델을 사용하기 위한 API key가 없습니다.", 503)
        data = _post_json(
            f"{groq.base_url}/chat/completions",
            {
                "model": groq.model,
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
            groq.model,
        )
