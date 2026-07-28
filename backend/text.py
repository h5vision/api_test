from __future__ import annotations


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    return [item["content"] for item in chunk_text_with_metadata(text, chunk_size, overlap)]


def chunk_text_with_metadata(
    text: str, chunk_size: int, overlap: int
) -> list[dict[str, int | str]]:
    cleaned = text.replace("\r\n", "\n").strip()
    if not cleaned:
        return []
    if len(cleaned) <= chunk_size:
        return [
            {
                "content": cleaned,
                "line_start": 1,
                "line_end": cleaned.count("\n") + 1,
            }
        ]

    chunks: list[dict[str, int | str]] = []
    start = 0
    while start < len(cleaned):
        hard_end = min(len(cleaned), start + chunk_size)
        end = hard_end
        if hard_end < len(cleaned):
            candidates = [
                cleaned.rfind("\n\n", start, hard_end),
                cleaned.rfind("\n", start, hard_end),
                cleaned.rfind(" ", start, hard_end),
            ]
            boundary = max(candidates)
            if boundary > start + chunk_size // 2:
                end = boundary
        chunk = cleaned[start:end].strip()
        if chunk:
            line_start = cleaned.count("\n", 0, start) + 1
            line_end = cleaned.count("\n", 0, end) + 1
            chunks.append(
                {
                    "content": chunk,
                    "line_start": line_start,
                    "line_end": line_end,
                }
            )
        if end >= len(cleaned):
            break
        start = max(start + 1, end - min(overlap, end - start - 1))
    return chunks
