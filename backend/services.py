from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

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
) -> dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "VisionBackend/1.0",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
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
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def embed(self, text: str, input_type: str) -> EmbeddingResult:
        return self.embed_many([text], input_type)[0]

    def embed_many(self, texts: list[str], input_type: str) -> list[EmbeddingResult]:
        if not texts or any(not text.strip() for text in texts):
            raise ServiceError("임베딩할 텍스트가 비어 있습니다.", status_code=400)
        if self.settings.embedding_provider == "ollama":
            return self._ollama_embeddings(texts)
        if self.settings.embedding_provider == "local":
            return [self._local_embedding(text) for text in texts]
        if not self.settings.embedding_api_key:
            if self.settings.allow_local_fallback:
                return [self._local_embedding(text) for text in texts]
            raise ServiceError("EMBEDDING_API_KEY 또는 NVIDIA_API_KEY가 필요합니다.", 503)

        try:
            payload = {
                "input": texts,
                "model": self.settings.embedding_model,
                "input_type": input_type,
                "encoding_format": "float",
                "truncate": "END",
            }
            data = _post_json(
                f"{self.settings.embedding_base_url}/embeddings",
                payload,
                self.settings.embedding_api_key,
                self.settings.request_timeout_seconds,
            )
            rows = data.get("data")
            if not isinstance(rows, list) or len(rows) != len(texts):
                raise ServiceError("임베딩 API 응답 개수가 요청과 일치하지 않습니다.")
            results: list[EmbeddingResult] = []
            for row in rows:
                embedding = row.get("embedding") if isinstance(row, dict) else None
                if not isinstance(embedding, list) or not embedding:
                    raise ServiceError("임베딩 API 응답에 벡터가 없습니다.")
                results.append(
                    EmbeddingResult(
                        vector=[float(value) for value in embedding],
                        provider=self.settings.embedding_provider,
                        model=self.settings.embedding_model,
                    )
                )
            return results
        except ServiceError:
            if self.settings.allow_local_fallback:
                return [self._local_embedding(text) for text in texts]
            raise

    def _ollama_embeddings(self, texts: list[str]) -> list[EmbeddingResult]:
        data = _post_json(
            f"{self.settings.embedding_base_url}/api/embed",
            {
                "model": self.settings.embedding_model,
                "input": texts,
                "truncate": False,
                "keep_alive": self.settings.embedding_keep_alive,
            },
            "",
            self.settings.embedding_timeout_seconds,
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

    @staticmethod
    def _local_embedding(text: str) -> EmbeddingResult:
        dimensions = 384
        vector = [0.0] * dimensions
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[가-힣]+|\d+", text.lower())
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return EmbeddingResult(vector=vector, provider="local", model="hashed-lexical-v1")


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

        context_parts = []
        for index, source in enumerate(sources, start=1):
            label = source.path or source.document_id
            context_parts.append(f"[{index}] {label}\n{source.text}")
        context = "\n\n".join(context_parts) or "검색된 프로젝트 문서가 없습니다."
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "당신은 VS Code 안에서 동작하는 프로젝트 코드 어시스턴트입니다. "
                    "제공된 프로젝트 문서를 우선 근거로 사용하고, 근거가 있는 문장은 [1] 같은 번호로 표시하세요. "
                    "문서에서 확인할 수 없는 내용은 추측이라고 명확히 밝히세요. "
                    "코드 질문에는 간결하고 실행 가능한 답을 한국어로 작성하세요."
                ),
            }
        ]
        for item in history[-10:]:
            messages.append({"role": item.role, "content": item.content})
        messages.append(
            {
                "role": "user",
                "content": f"프로젝트 검색 결과:\n{context}\n\n질문:\n{question}",
            }
        )
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
