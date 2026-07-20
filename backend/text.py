from __future__ import annotations


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    cleaned = text.replace("\r\n", "\n").strip()
    if not cleaned:
        return []
    if len(cleaned) <= chunk_size:
        return [cleaned]

    chunks: list[str] = []
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
            chunks.append(chunk)
        if end >= len(cleaned):
            break
        start = max(start + 1, end - min(overlap, end - start - 1))
    return chunks

