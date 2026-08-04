from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any


_TRANSLATED_DOC_PATTERN = re.compile(
    r"^docs/(?P<locale>[a-z]{2,3}(?:-[a-z]{2,4})?)/docs/",
    re.IGNORECASE,
)
_MARKDOWN_BOUNDARY = re.compile(r"(?m)^#{1,6}\s+\S")
_PYTHON_BOUNDARY = re.compile(r"(?m)^(?:async\s+def|def|class)\s+[A-Za-z_]\w*")
_SCRIPT_BOUNDARY = re.compile(
    r"(?m)^(?:export\s+)?(?:default\s+)?(?:async\s+)?"
    r"(?:class|function|interface|type|enum|namespace)\s+[A-Za-z_$][\w$]*"
    r"|^(?:export\s+)?(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*="
)
_JVM_BOUNDARY = re.compile(
    r"(?m)^\s*(?:public|protected|private|internal|static|final|abstract|open|suspend|data|sealed|\s)+"
    r"(?:class|interface|enum|record|fun|void|[A-Za-z_$][\w$<>?, ]+)\s+[A-Za-z_$][\w$]*"
)
_WORD_PATTERN = re.compile(r"[\w\u3040-\u30ff\u3400-\u9fff\u0400-\u04ff]+", re.UNICODE)

_CODE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".cxx", ".go", ".h", ".hpp", ".java",
    ".js", ".jsx", ".kt", ".kts", ".mjs", ".php", ".py", ".rb", ".rs",
    ".sh", ".sql", ".svelte", ".swift", ".ts", ".tsx", ".vue",
}
_CONFIG_SUFFIXES = {
    ".cfg", ".conf", ".env", ".ini", ".json", ".properties", ".toml",
    ".xml", ".yaml", ".yml",
}
_RELEASE_NAMES = {
    "changelog", "changelog.md", "changes", "changes.md", "history.md",
    "news.md", "release-notes.md", "releases.md",
}
_COMMUNITY_PATH_PARTS = {
    "discussion_template",
    "issue_template",
    "pull_request_template",
}


def classify_index_path(path: str | None, language: str | None = None) -> dict[str, Any]:
    """Return stable retrieval metadata inferred from a repository-relative path."""

    normalized = (path or "").replace("\\", "/").lstrip("./")
    lowered = normalized.casefold()
    pure = PurePosixPath(normalized or "unknown")
    suffix = pure.suffix.casefold()
    name = pure.name.casefold()
    translated = _TRANSLATED_DOC_PATTERN.match(lowered)
    locale = translated.group("locale").casefold() if translated else None
    path_parts = set(part.casefold() for part in pure.parts)

    if path_parts & _COMMUNITY_PATH_PARTS or name in {
        "code_of_conduct.md",
        "contributing.md",
        "support.md",
    }:
        category = "community"
        content_type = "documentation"
    elif name in _RELEASE_NAMES:
        category = "release_notes"
        content_type = "documentation"
    elif "test" in path_parts or "tests" in path_parts or name.startswith("test_"):
        category = "tests"
        content_type = "code"
    elif translated:
        category = "translated_docs"
        content_type = "documentation"
    elif lowered.startswith("docs/") or suffix in {".md", ".rst", ".txt"}:
        category = "docs"
        content_type = "documentation"
    elif suffix in _CODE_SUFFIXES:
        category = "code"
        content_type = "code"
    elif suffix in _CONFIG_SUFFIXES or name in {
        "dockerfile", "makefile", "procfile", "gemfile",
    }:
        category = "config"
        content_type = "configuration"
    else:
        category = "other"
        content_type = language or "text"

    return {
        "content_type": content_type,
        "path_category": category,
        "locale": locale,
        "is_translation": translated is not None,
    }


def is_low_information_chunk(text: str) -> bool:
    """Reject punctuation, template debris and tiny fragments before embedding."""

    normalized = text.strip()
    if not normalized:
        return True
    words = _WORD_PATTERN.findall(normalized)
    informative_chars = sum(len(word) for word in words)
    if informative_chars == 0:
        return True
    if len(normalized) < 16 and (len(words) < 2 or informative_chars < 8):
        return True
    visible_chars = sum(not character.isspace() for character in normalized)
    punctuation_chars = max(0, visible_chars - informative_chars)
    if visible_chars and punctuation_chars / visible_chars >= 0.88:
        return True
    return False


def _plain_chunks(
    text: str,
    chunk_size: int,
    overlap: int,
    *,
    base_offset: int = 0,
) -> list[tuple[int, int, str]]:
    chunks: list[tuple[int, int, str]] = []
    start = 0
    while start < len(text):
        hard_end = min(len(text), start + chunk_size)
        end = hard_end
        if hard_end < len(text):
            candidates = [
                text.rfind("\n\n", start, hard_end),
                text.rfind("\n", start, hard_end),
                text.rfind(" ", start, hard_end),
            ]
            boundary = max(candidates)
            if boundary > start + chunk_size // 2:
                end = boundary
        raw = text[start:end]
        stripped = raw.strip()
        if stripped:
            leading = len(raw) - len(raw.lstrip())
            trailing = len(raw.rstrip())
            chunks.append(
                (base_offset + start + leading, base_offset + start + trailing, stripped)
            )
        if end >= len(text):
            break
        start = max(start + 1, end - min(overlap, end - start - 1))
    return chunks


def _boundary_pattern(path: str | None, language: str | None) -> re.Pattern[str] | None:
    suffix = PurePosixPath((path or "").replace("\\", "/")).suffix.casefold()
    normalized_language = (language or "").casefold()
    if suffix in {".md", ".rst"} or normalized_language in {"markdown", "rst"}:
        return _MARKDOWN_BOUNDARY
    if suffix == ".py" or normalized_language == "python":
        return _PYTHON_BOUNDARY
    if suffix in {".js", ".jsx", ".mjs", ".ts", ".tsx"} or normalized_language in {
        "javascript", "javascriptreact", "typescript", "typescriptreact",
    }:
        return _SCRIPT_BOUNDARY
    if suffix in {".java", ".kt", ".kts", ".cs"} or normalized_language in {
        "java", "kotlin", "csharp",
    }:
        return _JVM_BOUNDARY
    return None


def _structured_chunks(
    text: str,
    chunk_size: int,
    overlap: int,
    *,
    path: str | None,
    language: str | None,
) -> tuple[list[tuple[int, int, str]], str]:
    pattern = _boundary_pattern(path, language)
    if pattern is None:
        return _plain_chunks(text, chunk_size, overlap), "character-boundary-v1"

    boundaries = sorted({0, *(match.start() for match in pattern.finditer(text))})
    if len(boundaries) <= 1:
        return _plain_chunks(text, chunk_size, overlap), "character-boundary-v1"
    boundaries.append(len(text))
    sections = [
        (boundaries[index], boundaries[index + 1])
        for index in range(len(boundaries) - 1)
        if text[boundaries[index]:boundaries[index + 1]].strip()
    ]

    chunks: list[tuple[int, int, str]] = []
    packed_start: int | None = None
    packed_end: int | None = None
    for section_start, section_end in sections:
        section_length = section_end - section_start
        if section_length > chunk_size:
            if packed_start is not None and packed_end is not None:
                chunks.extend(_plain_chunks(text[packed_start:packed_end], chunk_size, overlap, base_offset=packed_start))
                packed_start = packed_end = None
            chunks.extend(_plain_chunks(text[section_start:section_end], chunk_size, overlap, base_offset=section_start))
            continue
        if packed_start is None:
            packed_start, packed_end = section_start, section_end
            continue
        if section_end - packed_start <= chunk_size:
            packed_end = section_end
            continue
        chunks.extend(_plain_chunks(text[packed_start:packed_end], chunk_size, 0, base_offset=packed_start))
        packed_start, packed_end = section_start, section_end
    if packed_start is not None and packed_end is not None:
        chunks.extend(_plain_chunks(text[packed_start:packed_end], chunk_size, 0, base_offset=packed_start))
    return chunks, "structural-boundary-v1"


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    return [item["content"] for item in chunk_text_with_metadata(text, chunk_size, overlap)]


def chunk_text_with_metadata(
    text: str,
    chunk_size: int,
    overlap: int,
    *,
    path: str | None = None,
    language: str | None = None,
) -> list[dict[str, Any]]:
    cleaned = text.replace("\r\n", "\n").strip()
    if not cleaned:
        return []

    raw_chunks, strategy = _structured_chunks(
        cleaned,
        chunk_size,
        overlap,
        path=path,
        language=language,
    )
    classification = classify_index_path(path, language)
    chunks: list[dict[str, Any]] = []
    for start, end, content in raw_chunks:
        if is_low_information_chunk(content):
            continue
        chunks.append(
            {
                "content": content,
                "line_start": cleaned.count("\n", 0, start) + 1,
                "line_end": cleaned.count("\n", 0, end) + 1,
                "chunking_strategy": strategy,
                **classification,
            }
        )
    return chunks
