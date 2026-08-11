from __future__ import annotations

from typing import Any

from ...services import ServiceError


class GenerationExecutionCommonMixin:
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

    @staticmethod
    def _validate_external_messages(
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        validated: list[dict[str, str]] = []
        for item in messages:
            if not isinstance(item, dict):
                raise ServiceError("외부 Prompt message 형식이 올바르지 않습니다.", 502)
            role = item.get("role")
            content = item.get("content")
            if role not in {"system", "user", "assistant"}:
                raise ServiceError("외부 Prompt message role이 올바르지 않습니다.", 502)
            if not isinstance(content, str) or not content.strip():
                raise ServiceError("외부 Prompt message content가 비어 있습니다.", 502)
            validated.append({"role": role, "content": content})
        if not validated:
            raise ServiceError("외부 Prompt가 messages를 반환하지 않았습니다.", 502)
        return validated
