from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .config import Settings
from .schemas import HistoryMessage, Source


class ServiceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


def _post_json(
    url: str,
    payload: dict[str, Any],
    api_key: str,
    timeout: int,
    auth_type: str = "bearer",
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "VisionBackend/1.0",
    }
    if api_key and auth_type == "x-api-key":
        headers["X-API-Key"] = api_key
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise ServiceError(f"외부 AI API가 HTTP {exc.code}을 반환했습니다: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ServiceError(f"외부 AI API에 연결할 수 없습니다: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ServiceError("외부 AI API 응답이 올바른 JSON이 아닙니다.") from exc


@dataclass(frozen=True)
class EmbeddingResult:
    vector: list[float]
    provider: str
    model: str


class EmbeddingService:
    def __init__(
        self,
        settings: Settings,
        provider_resolver: Callable[[str], Any] | None = None,
    ) -> None:
        self.settings = settings
        self._provider_resolver = provider_resolver

    def _provider_connection(self) -> tuple[str, str, str, str]:
        provider_id = str(getattr(self.settings, "embedding_provider_id", "") or "")
        if not provider_id:
            return (
                self.settings.embedding_provider,
                self.settings.embedding_base_url,
                self.settings.embedding_api_key,
                "bearer",
            )
        if self._provider_resolver is None:
            raise ServiceError("선택된 Embedding Provider를 조회할 수 없습니다.", 503)
        try:
            provider = self._provider_resolver(provider_id)
        except Exception as exc:
            raise ServiceError("Embedding Provider 설정 조회에 실패했습니다.", 503) from exc
        if provider is None or not getattr(provider, "enabled", False):
            raise ServiceError(
                "선택된 Embedding Provider가 없거나 비활성화되어 있습니다.", 503
            )
        return (
            str(provider.protocol),
            str(provider.base_url).rstrip("/"),
            str(provider.api_key or ""),
            str(provider.auth_type),
        )

    def embed(self, text: str, input_type: str) -> EmbeddingResult:
        return self.embed_many([text], input_type)[0]

    def embed_many(self, texts: list[str], input_type: str) -> list[EmbeddingResult]:
        if not texts or any(not text.strip() for text in texts):
            raise ServiceError("임베딩할 텍스트가 비어 있습니다.", status_code=400)
        provider, base_url, api_key, auth_type = self._provider_connection()
        provider = provider.strip().lower()
        if provider == "ollama":
            return self._ollama_embeddings(
                texts,
                base_url=base_url,
                api_key=api_key,
                auth_type=auth_type,
            )
        if provider not in {"openai", "nvidia"}:
            raise ServiceError(
                f"지원하지 않는 embedding provider입니다: {provider}",
                status_code=422,
            )
        if not api_key:
            raise ServiceError(
                "선택한 embedding provider의 API key가 구성되어 있지 않습니다.",
                503,
            )

        payload = {
            "input": texts,
            "model": self.settings.embedding_model,
            "input_type": input_type,
            "encoding_format": "float",
            "truncate": "END",
        }
        data = _post_json(
            f"{base_url}/embeddings",
            payload,
            api_key,
            self.settings.request_timeout_seconds,
            auth_type,
        )
        rows = data.get("data")
        if not isinstance(rows, list) or len(rows) != len(texts):
            raise ServiceError("임베딩 API 응답 개수가 요청과 일치하지 않습니다.")
        results: list[EmbeddingResult] = []
        for row in rows:
            embedding = row.get("embedding") if isinstance(row, dict) else None
            if not isinstance(embedding, list) or not embedding:
                raise ServiceError("임베딩 API 응답에 벡터가 없습니다.")
            vector = [float(value) for value in embedding]
            if len(vector) != self.settings.embedding_dimension:
                raise ServiceError(
                    "Embedding 차원이 관리자 설정과 일치하지 않습니다: "
                    f"expected={self.settings.embedding_dimension}, actual={len(vector)}"
                )
            results.append(
                EmbeddingResult(
                    vector=vector,
                    provider=provider,
                    model=self.settings.embedding_model,
                )
            )
        return results

    def _ollama_embeddings(
        self,
        texts: list[str],
        *,
        base_url: str,
        api_key: str,
        auth_type: str,
    ) -> list[EmbeddingResult]:
        data = _post_json(
            f"{base_url}/api/embed",
            {
                "model": self.settings.embedding_model,
                "input": texts,
                "truncate": False,
                "keep_alive": self.settings.embedding_keep_alive,
            },
            api_key,
            self.settings.embedding_timeout_seconds,
            auth_type,
        )
        embeddings = data.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise ServiceError("Ollama embedding 응답 개수가 요청과 일치하지 않습니다.")
        results: list[EmbeddingResult] = []
        for embedding in embeddings:
            if not isinstance(embedding, list) or not embedding:
                raise ServiceError("Ollama embedding 응답에 벡터가 없습니다.")
            vector = [float(value) for value in embedding]
            if len(vector) != self.settings.embedding_dimension:
                raise ServiceError(
                    "Ollama embedding 차원이 설정과 일치하지 않습니다: "
                    f"expected={self.settings.embedding_dimension}, actual={len(vector)}"
                )
            results.append(
                EmbeddingResult(
                    vector=vector,
                    provider="ollama",
                    model=self.settings.embedding_model,
                )
            )
        return results


class ChatService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def answer(
        self,
        question: str,
        sources: list[Source],
        history: list[HistoryMessage],
    ) -> tuple[str, str]:
        if self.settings.ai_provider == "local":
            return self._local_answer(question, sources), "local"
        if not self.settings.ai_api_key:
            if self.settings.allow_local_fallback:
                return self._local_answer(question, sources), "local-fallback"
            raise ServiceError("AI_API_KEY 또는 NVIDIA_API_KEY가 필요합니다.", 503)

        # Legacy hidden endpoint: forward only client-authored conversation data.
        # Vision no longer owns or injects an AI system/RAG prompt.
        messages: list[dict[str, str]] = []
        for item in history[-10:]:
            messages.append({"role": item.role, "content": item.content})
        messages.append({"role": "user", "content": question})
        try:
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
            content = data.get("choices", [{}])[0].get("message", {}).get("content")
            if not isinstance(content, str) or not content.strip():
                raise ServiceError("AI API 응답에 답변 텍스트가 없습니다.")
            return content.strip(), self.settings.ai_provider
        except ServiceError:
            if self.settings.allow_local_fallback:
                return self._local_answer(question, sources), "local-fallback"
            raise

    @staticmethod
    def _local_answer(question: str, sources: list[Source]) -> str:
        if not sources:
            return (
                "현재 프로젝트에서 관련 문서를 찾지 못했습니다. "
                "VS Code에서 현재 파일을 먼저 인덱싱한 후 다시 질문해 주세요."
            )
        lines = [
            "AI API를 사용할 수 없어 검색 결과를 직접 반환합니다.",
            f"질문: {question}",
            "",
        ]
        for index, source in enumerate(sources[:3], start=1):
            label = source.path or source.document_id
            excerpt = source.text[:500].strip()
            lines.append(f"[{index}] {label}\n{excerpt}")
        return "\n\n".join(lines)
