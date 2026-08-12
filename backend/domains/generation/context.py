from __future__ import annotations

from ..chat.schemas import ChatContextItem, HistoryMessage

def passthrough_messages(
    question: str,
    history: list[HistoryMessage],
    frontend_context: str | list[ChatContextItem] = "",
) -> list[dict[str, str]]:
    """Forward client-authored conversation and attachments without a system prompt."""

    messages = [
        {"role": item.role, "content": item.content}
        for item in history[-20:]
        if item.content.strip()
    ]
    context_text, _images = _frontend_attachment_context(frontend_context)
    current_content = question
    if context_text:
        current_content = f"{question}\n\n[첨부 자료]\n{context_text}"
    messages.append({"role": "user", "content": current_content})
    return messages

def _frontend_attachment_context(
    frontend_context: str | list[ChatContextItem],
) -> tuple[str, list[str]]:
    """Extract text and Ollama-compatible image payloads from client context."""

    if isinstance(frontend_context, str):
        return frontend_context.strip(), []
    text_parts: list[str] = []
    images: list[str] = []
    for item in frontend_context:
        value = item.value
        name = (item.name or item.id or "attachment").strip()
        if isinstance(value, str):
            if value.strip():
                text_parts.append(f"--- {name} ---\n{value.strip()}")
            continue
        if not isinstance(value, dict):
            continue
        kind = str(value.get("kind") or "").strip().lower()
        if kind == "image":
            data_url = str(value.get("data_url") or "").strip()
            if data_url.startswith("data:image/") and "," in data_url:
                _header, encoded = data_url.split(",", 1)
                if encoded:
                    images.append(encoded)
            text_parts.append(
                f"--- 이미지 첨부: {value.get('file_name') or name} "
                f"({value.get('mime_type') or 'image'}) ---"
            )
            continue
        content = value.get("content")
        if not isinstance(content, str):
            content = value.get("text")
        if isinstance(content, str) and content.strip():
            language_label = str(value.get("language_id") or "").strip()
            ecosystem = str(value.get("ecosystem") or "").strip()
            language_detail = ""
            if language_label:
                language_detail = f" [language_id={language_label}"
                if ecosystem:
                    language_detail += f", ecosystem={ecosystem}"
                language_detail += "]"
            text_parts.append(
                f"--- {value.get('file_name') or name}{language_detail} ---\n{content.strip()}"
            )
    return "\n\n".join(text_parts), images
