from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from time import monotonic, perf_counter
from collections.abc import Callable, Iterator
from typing import Any
from uuid import uuid4

from .ai_providers import AIProviderRegistry, AIProviderStoreError
from .config import Settings
from .runtime_services import RuntimeGroqSettings
from .schemas import ModelInfo
from .services import ChatService, ServiceError, _post_json


@dataclass(frozen=True)
class GenerationResult:
    request_id: str
    answer: str
    requested_model_id: str
    used_model_id: str
    provider: str
    used_model_name: str
    inference_protocol: str | None = None
    inference_endpoint: str | None = None


@dataclass(frozen=True)
class StreamingGeneration:
    request_id: str
    requested_model_id: str
    used_model_id: str
    provider: str
    used_model_name: str
    inference_protocol: str
    inference_endpoint: str | None
    deltas: Iterator[str]


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
        self._nvidia_status_lock = threading.Lock()
        self._nvidia_status_cached_at = 0.0
        self._nvidia_status_cache: dict[str, Any] | None = None
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

    def invalidate_nvidia_status(self) -> None:
        with self._nvidia_status_lock:
            self._nvidia_status_cache = None
            self._nvidia_status_cached_at = 0.0

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

    @staticmethod
    def _probe_openai_catalog(
        base_url: str,
        api_key: str,
        *,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        if not api_key:
            return {
                "status": "offline",
                "connected": False,
                "model_available": False,
                "models": [],
                "latency_ms": 0,
                "error": "missing_api_key",
            }
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/models",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "VisionBackend/1.0",
            },
            method="GET",
        )
        started_at = perf_counter()
        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout_seconds,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
            entries = payload.get("data", [])
            if not isinstance(entries, list):
                raise ValueError("models is not a list")
            models = sorted(
                {
                    str(item.get("id") or "").strip()
                    for item in entries
                    if isinstance(item, dict)
                    and str(item.get("id") or "").strip()
                }
            )
            return {
                "status": "online" if models else "degraded",
                "connected": True,
                "model_available": bool(models),
                "models": models,
                "latency_ms": round((perf_counter() - started_at) * 1000),
                "error": None if models else "no_models",
            }
        except urllib.error.HTTPError as exc:
            return {
                "status": "offline",
                "connected": False,
                "model_available": False,
                "models": [],
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
                exc.reason,
                TimeoutError,
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

    def models(self) -> list[ModelInfo]:
        backendai_status = self.backendai_status()
        nvidia_status = self.nvidia_status()
        groq_status = self.groq_status()
        groq = self._groq_settings()
        default_model_id = groq.default_model_id
        backendai_models = backendai_status.get("models", [])
        nvidia_models = nvidia_status.get("models", [])
        groq_models = groq_status.get("models", [])
        selected_nvidia_model = self._preferred_catalog_model(
            self.settings.ai_model,
            nvidia_models,
        )
        selected_groq_model = self._preferred_catalog_model(
            groq.model,
            groq_models,
        )
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
                model_name=selected_nvidia_model or "auto-discovery",
                display_name=(
                    "NVIDIA Cloud "
                    f"({selected_nvidia_model or '모델 자동 감지 실패'})"
                ),
                provider="nvidia",
                location="cloud",
                deployment_type="cloud",
                endpoint=nvidia_endpoint,
                enabled=self._model_enabled(
                    self.settings.nvidia_public_model_id
                ),
                available=bool(selected_nvidia_model),
                is_default=(
                    default_model_id == self.settings.nvidia_public_model_id
                ),
                streaming=False,
            ),
            ModelInfo(
                model_id=self.settings.groq_public_model_id,
                model_name=selected_groq_model or "auto-discovery",
                display_name=(
                    f"Groq Cloud ({selected_groq_model or '모델 자동 감지 실패'})"
                ),
                provider="groq",
                location="cloud",
                deployment_type="cloud",
                endpoint=groq_endpoint,
                enabled=self._model_enabled(
                    self.settings.groq_public_model_id
                ),
                available=bool(selected_groq_model),
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
        for provider, model_names, endpoint in (
            ("nvidia", nvidia_models, nvidia_endpoint),
            ("groq", groq_models, groq_endpoint),
        ):
            for model_name in model_names:
                model_id = self._catalog_model_id(provider, model_name)
                models.append(
                    ModelInfo(
                        model_id=model_id,
                        model_name=model_name,
                        display_name=f"{provider.upper()} Cloud ({model_name})",
                        provider=provider,
                        location="cloud",
                        deployment_type="cloud",
                        endpoint=endpoint,
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
        messages: list[dict[str, str]],
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
        if requested == "local-test":
            question = messages[-1]["content"] if messages else ""
            answer, _ = self._legacy_local.answer(question, [], [])
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
                            "num_ctx": self.settings.ai_context_window_tokens,
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
                    "ollama",
                    self._endpoint_label(self._backendai_base_url()),
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
                    resolved_request_id,
                    requested,
                    requested_for_fallback,
                    messages,
                )
        nvidia_model = self._parse_catalog_model_id(requested, "nvidia")
        if requested == self.settings.nvidia_public_model_id:
            nvidia_status = self.nvidia_status()
            nvidia_model = self._preferred_catalog_model(
                self.settings.ai_model,
                nvidia_status.get("models", []),
            )
        if nvidia_model is not None:
            nvidia_models = self.nvidia_status().get("models", [])
            if nvidia_model not in nvidia_models:
                raise ServiceError(
                    "NVIDIA API에서 사용할 수 없는 model_id입니다: "
                    f"{requested}",
                    status_code=422,
                )
            return self._generate_nvidia(
                resolved_request_id,
                requested,
                requested,
                messages,
                model_name=nvidia_model,
            )
        groq_model = self._parse_catalog_model_id(requested, "groq")
        if requested == self.settings.groq_public_model_id:
            groq_status = self.groq_status()
            groq_model = self._preferred_catalog_model(
                self._groq_settings().model,
                groq_status.get("models", []),
            )
        if groq_model is not None:
            groq_models = self.groq_status().get("models", [])
            if groq_model not in groq_models:
                raise ServiceError(
                    "Groq API에서 사용할 수 없는 model_id입니다: "
                    f"{requested}",
                    status_code=422,
                )
            return self._generate_groq(
                resolved_request_id,
                requested,
                requested,
                messages,
                model_name=groq_model,
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

    def stream_backendai(
        self,
        requested_model_id: str | None,
        messages: list[dict[str, str]],
        *,
        request_id: str | None = None,
    ) -> StreamingGeneration:
        """Open an Ollama NDJSON stream for BackendAI models.

        Other providers keep the frozen non-stream generation path. The API
        layer can still wrap their completed answer in SSE for compatibility.
        """

        resolved_request_id = request_id or f"req_{uuid4().hex}"
        requested = requested_model_id or self.default_model_id
        if not self._model_enabled(requested):
            raise ServiceError(
                f"관리자가 API 사용을 비활성화한 model_id입니다: {requested}",
                status_code=403,
            )
        backendai_model: str | None = None
        if requested == self.settings.backendai_public_model_id:
            backendai_model = self.settings.backendai_model
        elif requested.startswith("backendai:"):
            backendai_model = urllib.parse.unquote(
                requested.removeprefix("backendai:").strip()
            )
        if not backendai_model:
            raise ServiceError(
                "선택한 모델은 Ollama 토큰 스트리밍 대상이 아닙니다.",
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
        url = f"{self._backendai_base_url()}/api/chat"
        payload = {
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

        def iterate() -> Iterator[str]:
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/x-ndjson",
                "User-Agent": "VisionBackend/1.0",
            }
            if self.settings.backendai_api_key:
                headers["Authorization"] = f"Bearer {self.settings.backendai_api_key}"
            upstream_request = urllib.request.Request(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            emitted = False
            try:
                with urllib.request.urlopen(
                    upstream_request,
                    timeout=self.settings.request_timeout_seconds,
                ) as response:
                    for raw_line in response:
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line:
                            continue
                        data = json.loads(line)
                        if data.get("error"):
                            raise ServiceError(
                                f"Ollama streaming error: {data['error']}",
                                status_code=502,
                            )
                        message = data.get("message")
                        content = message.get("content") if isinstance(message, dict) else None
                        if isinstance(content, str) and content:
                            emitted = True
                            yield content
                        if data.get("done"):
                            break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                raise ServiceError(
                    f"Ollama streaming HTTP {exc.code}: {detail}",
                    status_code=502,
                ) from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                raise ServiceError(
                    f"Ollama streaming 연결 실패: {exc}",
                    status_code=504 if isinstance(exc, TimeoutError) else 503,
                ) from exc
            except json.JSONDecodeError as exc:
                raise ServiceError("Ollama streaming 응답이 올바른 NDJSON이 아닙니다.") from exc
            if not emitted:
                raise ServiceError("Ollama streaming 응답에 텍스트가 없습니다.")

        return StreamingGeneration(
            request_id=resolved_request_id,
            requested_model_id=requested,
            used_model_id=requested,
            provider="backendai",
            used_model_name=backendai_model,
            inference_protocol="ollama",
            inference_endpoint=self._endpoint_label(self._backendai_base_url()),
            deltas=iterate(),
        )

    def _generate_nvidia(
        self,
        request_id: str,
        requested_model_id: str,
        used_model_id: str,
        messages: list[dict[str, str]],
        model_name: str | None = None,
    ) -> GenerationResult:
        if not self.settings.ai_api_key:
            raise ServiceError("NVIDIA 모델을 사용하기 위한 API key가 없습니다.", 503)
        resolved_model_name = model_name or self._preferred_catalog_model(
            self.settings.ai_model,
            self.nvidia_status().get("models", []),
        )
        if not resolved_model_name:
            raise ServiceError(
                "NVIDIA API에서 사용할 수 있는 모델을 자동 감지하지 못했습니다.",
                503,
            )
        data = _post_json(
            f"{self.settings.ai_base_url}/chat/completions",
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
            "openai-compatible",
            self._endpoint_label(self.settings.ai_base_url),
        )

    def _generate_groq(
        self,
        request_id: str,
        requested_model_id: str,
        used_model_id: str,
        messages: list[dict[str, str]],
        model_name: str | None = None,
    ) -> GenerationResult:
        groq = self._groq_settings()
        if not groq.enabled:
            raise ServiceError("Groq 모델이 관리자 설정에서 비활성화되어 있습니다.", 503)
        if not self.settings.groq_api_key:
            raise ServiceError("Groq 모델을 사용하기 위한 API key가 없습니다.", 503)
        resolved_model_name = model_name or self._preferred_catalog_model(
            groq.model,
            self.groq_status().get("models", []),
        )
        if not resolved_model_name:
            raise ServiceError(
                "Groq API에서 사용할 수 있는 모델을 자동 감지하지 못했습니다.",
                503,
            )
        data = _post_json(
            f"{groq.base_url}/chat/completions",
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
            "openai-compatible",
            self._endpoint_label(groq.base_url),
        )
