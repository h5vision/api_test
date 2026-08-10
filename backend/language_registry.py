from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, ConfigDict, Field


REGISTRY_PATH = Path(__file__).with_name("data") / "vscode_languages.json"


@dataclass(frozen=True)
class LanguageDetection:
    language_id: str
    display_name: str
    language: str
    dialect: str | None
    ecosystem: str | None
    runtime: str | None
    frameworks: tuple[str, ...]
    source: str
    confidence: float
    registry_revision: str
    evidence_sources: tuple[str, ...] = ()
    candidates: tuple[dict[str, Any], ...] = ()

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["frameworks"] = list(self.frameworks)
        value["evidence_sources"] = list(self.evidence_sources)
        value["candidates"] = [dict(candidate) for candidate in self.candidates]
        return value


class LanguageDetectRequest(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    language_id: str | None = Field(default=None, max_length=255)
    file_name: str | None = Field(default=None, max_length=4096)
    path: str | None = Field(default=None, max_length=4096)
    content: str | None = Field(default=None, max_length=1_000_000)
    workspace_languages: list[str] = Field(default_factory=list, max_length=100)
    session_languages: list[str] = Field(default_factory=list, max_length=100)
    workspace_history_languages: list[str] = Field(default_factory=list, max_length=100)
    global_history_languages: list[str] = Field(default_factory=list, max_length=100)


_ENRICHMENT: dict[str, tuple[str, str | None, str | None, str | None]] = {
    "typescriptreact": ("typescript", "tsx", "javascript", "node"),
    "javascriptreact": ("javascript", "jsx", "javascript", "node"),
    "typescript": ("typescript", None, "javascript", "node"),
    "javascript": ("javascript", None, "javascript", "node"),
    "python": ("python", None, "python", "python"),
    "shellscript": ("shell", None, "posix", "shell"),
    "powershell": ("powershell", None, ".net", "powershell"),
    "csharp": ("csharp", None, ".net", "dotnet"),
    "fsharp": ("fsharp", None, ".net", "dotnet"),
    "java": ("java", None, "jvm", "jvm"),
    "kotlin": ("kotlin", None, "jvm", "jvm"),
    "scala": ("scala", None, "jvm", "jvm"),
    "go": ("go", None, "go", "go"),
    "rust": ("rust", None, "rust", "rust"),
    "php": ("php", None, "php", "php"),
    "ruby": ("ruby", None, "ruby", "ruby"),
    "dart": ("dart", None, "dart", "dart"),
    "cuda-cpp": ("cpp", "cuda", "native", "cuda"),
}

_COMMON_LANGUAGE_ALIASES = {
    "js": "javascript",
    "jsx": "javascriptreact",
    "ts": "typescript",
    "tsx": "typescriptreact",
    "py": "python",
    "sh": "shellscript",
    "bash": "shellscript",
    "zsh": "shellscript",
    "ps1": "powershell",
    "cs": "csharp",
    "c#": "csharp",
    "fs": "fsharp",
    "cpp": "cpp",
    "c++": "cpp",
    "rb": "ruby",
    "rs": "rust",
}


def _frameworks(path: str, content: str) -> tuple[str, ...]:
    lowered_path = path.casefold()
    lowered = content[:200_000].casefold()
    found: list[str] = []
    probes = (
        ("react", ("react", "react-dom", "jsx", "tsx")),
        ("next.js", ("next.config", '"next"', "from 'next/")),
        ("vue", ("vue", "vue.config", "<template>")),
        ("svelte", ("svelte", "svelte.config")),
        ("fastapi", ("fastapi", "from fastapi", "import fastapi")),
        ("django", ("django", "manage.py", "django.conf")),
        ("flask", ("from flask", "import flask")),
        ("spring", ("spring-boot", "org.springframework")),
        ("asp.net", ("microsoft.aspnetcore", "aspnetcore")),
    )
    haystack = f"{lowered_path}\n{lowered}"
    for name, needles in probes:
        if any(needle in haystack for needle in needles):
            found.append(name)
    return tuple(found)


def _normalized_path(value: str | None) -> str:
    if not value:
        return ""
    candidate = value.strip()
    if candidate.startswith("file:"):
        parsed = urlparse(candidate)
        candidate = unquote(parsed.path)
    return candidate.replace("\\", "/")


def _content_candidates(content: str) -> dict[str, float]:
    sample = content.replace("\r\n", "\n")[:100_000]
    if len(sample.strip()) < 20:
        return {}
    stripped = sample.lstrip()
    probes: tuple[tuple[str, float, re.Pattern[str]], ...] = (
        ("php", 0.98, re.compile(r"^<\?php\b", re.IGNORECASE)),
        ("html", 0.96, re.compile(r"^<!doctype\s+html\b|^<html\b", re.IGNORECASE)),
        ("xml", 0.96, re.compile(r"^<\?xml\s", re.IGNORECASE)),
        ("python", 0.88, re.compile(r"(?m)^(?:async\s+)?def\s+\w+\s*\(|^class\s+\w+.*:\s*$")),
        ("go", 0.94, re.compile(r"(?m)^package\s+\w+\s*$[\s\S]*^import\s*(?:\(|\")")),
        ("rust", 0.88, re.compile(r"(?m)^\s*(?:pub\s+)?fn\s+\w+\s*\([^)]*\)\s*(?:->[^\{]+)?\{")),
        ("csharp", 0.92, re.compile(r"(?m)^using\s+System(?:\.|;)")),
        ("java", 0.72, re.compile(r"(?m)^\s*(?:public\s+)?(?:class|interface|record)\s+\w+")),
        ("sql", 0.9, re.compile(r"(?is)^\s*(?:select\b.+\bfrom\b|insert\s+into\b|create\s+table\b)")),
        ("typescript", 0.84, re.compile(r"(?m)(?:\binterface\s+\w+|\btype\s+\w+\s*=|\w+\s*:\s*[A-Za-z_$][\w.$<>\[\]| ]*)")),
        ("javascript", 0.62, re.compile(r"(?m)(?:\b(?:const|let|var)\s+\w+|\bfunction\s+\w+|=>)")),
    )
    candidates: dict[str, float] = {}
    for language_id, confidence, expression in probes:
        if expression.search(stripped):
            candidates[language_id] = max(candidates.get(language_id, 0.0), confidence)
    if "typescript" in candidates:
        candidates["javascript"] = max(candidates.get("javascript", 0.0), 0.52)
    elif "javascript" in candidates:
        candidates["typescript"] = max(candidates.get("typescript", 0.0), 0.45)
    if stripped.startswith(("{", "[")):
        try:
            json.loads(stripped)
            candidates["json"] = 0.98
        except json.JSONDecodeError:
            pass
    return candidates


class LanguageRegistry:
    def __init__(self, registry_path: Path = REGISTRY_PATH) -> None:
        self._catalog = json.loads(registry_path.read_text(encoding="utf-8"))
        self.languages: list[dict[str, Any]] = self._catalog["languages"]
        self.by_id = {item["id"]: item for item in self.languages}
        self.aliases: dict[str, str] = {}
        self.filenames: dict[str, str] = {}
        self.extensions: list[tuple[str, str]] = []
        self.patterns: list[tuple[str, str]] = []
        self.first_lines: list[tuple[re.Pattern[str], str]] = []
        for item in self.languages:
            language_id = item["id"]
            self.aliases.setdefault(language_id.casefold(), language_id)
            for alias in item.get("aliases", []):
                self.aliases.setdefault(alias.casefold(), language_id)
            for filename in item.get("filenames", []):
                self.filenames.setdefault(filename.casefold(), language_id)
            self.extensions.extend(
                (extension.casefold(), language_id)
                for extension in item.get("extensions", [])
            )
            self.patterns.extend(
                (pattern, language_id)
                for pattern in item.get("filename_patterns", [])
            )
            for expression in item.get("first_lines", []):
                try:
                    self.first_lines.append((re.compile(expression), language_id))
                except re.error:
                    continue
        for alias, language_id in _COMMON_LANGUAGE_ALIASES.items():
            if language_id in self.by_id:
                self.aliases[alias] = language_id
        self.extensions.sort(key=lambda item: len(item[0]), reverse=True)

    @property
    def revision(self) -> str:
        return str(self._catalog["registry_revision"])

    def catalog(self) -> dict[str, Any]:
        return self._catalog

    def _result(
        self,
        language_id: str,
        *,
        source: str,
        confidence: float,
        path: str,
        content: str,
        evidence_sources: tuple[str, ...] = (),
        candidates: tuple[dict[str, Any], ...] = (),
    ) -> LanguageDetection:
        item = self.by_id.get(language_id)
        display_name = (
            next(iter(item.get("aliases", [])), language_id) if item else language_id
        )
        language, dialect, ecosystem, runtime = _ENRICHMENT.get(
            language_id,
            (language_id, None, None, None),
        )
        return LanguageDetection(
            language_id=language_id,
            display_name=display_name,
            language=language,
            dialect=dialect,
            ecosystem=ecosystem,
            runtime=runtime,
            frameworks=_frameworks(path, content),
            source=source,
            confidence=confidence,
            registry_revision=self.revision,
            evidence_sources=evidence_sources or (source,),
            candidates=candidates,
        )

    def canonical_id(self, value: str) -> str:
        candidate = value.strip()
        return (
            candidate
            if candidate in self.by_id
            else self.aliases.get(candidate.casefold(), candidate)
        )

    def detect(
        self,
        *,
        explicit_language_id: str | None = None,
        file_name: str | None = None,
        path: str | None = None,
        content: str | None = None,
        workspace_languages: list[str] | tuple[str, ...] = (),
        session_languages: list[str] | tuple[str, ...] = (),
        workspace_history_languages: list[str] | tuple[str, ...] = (),
        global_history_languages: list[str] | tuple[str, ...] = (),
    ) -> LanguageDetection:
        normalized_path = _normalized_path(path or file_name)
        basename = PurePosixPath(normalized_path).name or (file_name or "")
        text = content or ""
        explicit = (explicit_language_id or "").strip()
        scores: dict[str, float] = {}
        evidence: dict[str, list[str]] = {}
        strongest: dict[str, tuple[float, str, float]] = {}

        def add(language_id: str, score: float, source: str, confidence: float) -> None:
            canonical = self.canonical_id(language_id)
            scores[canonical] = scores.get(canonical, 0.0) + score
            if source not in evidence.setdefault(canonical, []):
                evidence[canonical].append(source)
            if score > strongest.get(canonical, (-1.0, "fallback", 0.0))[0]:
                strongest[canonical] = (score, source, confidence)

        if explicit:
            # Extension-contributed IDs not in the built-in snapshot remain valid.
            add(explicit, 100.0, "explicit", 1.0)
        by_filename = self.filenames.get(basename.casefold())
        if by_filename:
            add(by_filename, 80.0, "filename", 0.99)
        for pattern, language_id in self.patterns:
            if fnmatch.fnmatch(normalized_path, pattern) or fnmatch.fnmatch(basename, pattern):
                add(language_id, 78.0, "filename_pattern", 0.96)
        lowered_name = basename.casefold()
        for extension, language_id in self.extensions:
            if lowered_name.endswith(extension):
                add(language_id, 76.0, "extension", 0.94)
                break
        first_line = text.splitlines()[0] if text else ""
        for expression, language_id in self.first_lines:
            try:
                if expression.search(first_line):
                    add(language_id, 70.0, "first_line", 0.9)
            except (re.error, RuntimeError):
                continue
        for language_id, confidence in _content_candidates(text).items():
            add(language_id, confidence * 50.0, "content", confidence)

        # Bias only reorders languages for which the document supplied evidence.
        # This prevents an empty document from becoming the workspace's dominant language.
        for languages, weight, source in (
            (session_languages, 7.0, "session_bias"),
            (workspace_languages, 5.0, "workspace_bias"),
            (workspace_history_languages, 3.0, "workspace_history_bias"),
            (global_history_languages, 1.0, "global_history_bias"),
        ):
            for language_id in dict.fromkeys(self.canonical_id(value) for value in languages if value):
                if language_id in scores:
                    add(language_id, weight, source, 0.0)

        if not scores:
            return self._result(
                "plaintext",
                source="fallback",
                confidence=0.2,
                path=normalized_path,
                content=text,
                evidence_sources=("fallback",),
                candidates=(),
            )
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        top_id, top_score = ranked[0]
        top_source = strongest[top_id][1]
        base_confidence = strongest[top_id][2]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        separation = (top_score - second_score) / max(top_score, 1.0)
        confidence = min(1.0, max(base_confidence, 0.5 + separation * 0.45))
        total_score = sum(score for _language_id, score in ranked)
        candidates = tuple(
            {
                "language_id": language_id,
                "score": round(score, 4),
                "confidence": round(score / max(total_score, 1.0), 4),
                "sources": evidence[language_id],
            }
            for language_id, score in ranked[:8]
        )
        return self._result(
            top_id,
            source=top_source,
            confidence=round(confidence, 4),
            path=normalized_path,
            content=text,
            evidence_sources=tuple(evidence[top_id]),
            candidates=candidates,
        )


@lru_cache(maxsize=1)
def language_registry() -> LanguageRegistry:
    return LanguageRegistry()


def _first_string(*values: Any) -> str:
    return next((value.strip() for value in values if isinstance(value, str) and value.strip()), "")


def _language_list(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(key) for key, _count in sorted(value.items(), key=lambda item: -float(item[1] or 0))]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


_FENCED_CODE_PATTERN = re.compile(
    r"(?ms)^```\s*([A-Za-z0-9_+.#-]*)\s*\n(.*?)^```\s*$"
)
_CODE_LINE_PATTERN = re.compile(
    r"(?:^\s*(?:async\s+)?def\s+|^\s*(?:const|let|var|function|class|interface|type|import|export|package|using|fn)\b|=>|[;{}]\s*$|<\/?[A-Za-z][^>]*>)"
)


def _message_code_segments(message: str) -> list[dict[str, Any]]:
    if not message.strip():
        return []
    segments: list[dict[str, Any]] = []
    masked = list(message)
    for index, match in enumerate(_FENCED_CODE_PATTERN.finditer(message)):
        code = match.group(2).strip()
        if code:
            segments.append(
                {
                    "scope": "code_fence",
                    "segment": index,
                    "declared_language": match.group(1).strip() or None,
                    "content": code[:100_000],
                    "start": match.start(2),
                    "end": match.end(2),
                }
            )
        for position in range(match.start(), match.end()):
            masked[position] = " " if masked[position] != "\n" else "\n"
    remainder = "".join(masked)
    for match in re.finditer(r"(?s)(?:^|\n\s*\n)(.*?)(?=\n\s*\n|\Z)", remainder):
        block = match.group(1).strip()
        if len(block) < 20 or len(block) > 100_000:
            continue
        code_lines = sum(bool(_CODE_LINE_PATTERN.search(line)) for line in block.splitlines())
        if code_lines < 1 or not _content_candidates(block):
            continue
        segments.append(
            {
                "scope": "pasted_code",
                "segment": len(segments),
                "declared_language": None,
                "content": block,
                "start": match.start(1),
                "end": match.end(1),
            }
        )
    return segments


def _payload_biases(payload: Any, workspace_detected: list[str] | None = None) -> dict[str, list[str]]:
    extras = getattr(payload, "model_extra", None) or {}
    workspace = list(workspace_detected or [])
    for key in ("workspace_languages", "workspaceLanguageIds", "language_distribution", "languageDistribution"):
        workspace.extend(_language_list(extras.get(key)))
    session = []
    for history_item in getattr(payload, "history", []) or []:
        for segment in _message_code_segments(str(getattr(history_item, "content", "") or "")):
            declared = segment["declared_language"]
            result = language_registry().detect(
                explicit_language_id=declared,
                content=segment["content"],
            )
            if result.language_id != "plaintext":
                session.append(result.language_id)
    session.extend(_language_list(extras.get("session_languages")))
    return {
        "workspace": list(dict.fromkeys(workspace))[:100],
        "session": list(dict.fromkeys(session))[:100],
        "workspace_history": _language_list(extras.get("workspace_history_languages"))[:100],
        "global_history": _language_list(extras.get("global_history_languages"))[:100],
    }


def normalize_chat_context_languages(payload: Any) -> Any:
    context = getattr(payload, "context", "")
    if isinstance(context, str):
        return payload
    normalized_by_index: dict[int, Any] = {}
    registry = language_registry()
    descriptors: list[tuple[int, Any, dict[str, Any], dict[str, Any], str, str, str]] = []
    preliminary_languages: list[str] = []
    for item_index, item in enumerate(context):
        raw = item.model_dump(mode="python")
        value = raw.get("value")
        if not isinstance(value, dict):
            value = {"kind": "text", "content": value} if isinstance(value, str) else {"raw_value": value}
        kind = str(value.get("kind") or "").casefold()
        if kind == "image":
            normalized_by_index[item_index] = item
            continue
        explicit = _first_string(
            raw.get("language_id"), raw.get("languageId"), raw.get("language"),
            value.get("language_id"), value.get("languageId"), value.get("language"),
        )
        path = _first_string(
            raw.get("path"), raw.get("fsPath"), raw.get("uri"),
            value.get("path"), value.get("fsPath"), value.get("uri"),
            value.get("file_name"), raw.get("name"), raw.get("id"),
        )
        content = _first_string(value.get("content"), value.get("text"))
        file_name = _first_string(value.get("file_name"), raw.get("name"))
        preliminary = registry.detect(
            explicit_language_id=explicit or None,
            file_name=file_name or None,
            path=path or None,
            content=content or None,
        )
        if preliminary.language_id != "plaintext":
            preliminary_languages.append(preliminary.language_id)
        descriptors.append((item_index, item, raw, value, explicit, path, content))
    biases = _payload_biases(payload, preliminary_languages)
    for item_index, item, raw, value, explicit, path, content in descriptors:
        detection = registry.detect(
            explicit_language_id=explicit or None,
            file_name=_first_string(value.get("file_name"), raw.get("name")) or None,
            path=path or None,
            content=content or None,
            workspace_languages=biases["workspace"],
            session_languages=biases["session"],
            workspace_history_languages=biases["workspace_history"],
            global_history_languages=biases["global_history"],
        )
        value = {
            **value,
            "language_id": detection.language_id,
            "language": detection.language,
            "dialect": detection.dialect,
            "ecosystem": detection.ecosystem,
            "runtime": detection.runtime,
            "frameworks": list(detection.frameworks),
            "language_detection": {
                "source": detection.source,
                "confidence": detection.confidence,
                "registry_revision": detection.registry_revision,
                "evidence_sources": list(detection.evidence_sources),
                "candidates": [dict(candidate) for candidate in detection.candidates],
            },
        }
        raw["value"] = value
        normalized_by_index[item_index] = item.__class__.model_validate(raw)
    normalized = [normalized_by_index[index] for index in range(len(context))]
    return payload.model_copy(update={"context": normalized})


def context_language_metadata(payload: Any) -> list[dict[str, Any]]:
    context = getattr(payload, "context", "")
    if isinstance(context, str):
        return []
    values = []
    for item in context:
        if not isinstance(item.value, dict) or not item.value.get("language_id"):
            continue
        values.append(
            {
                "name": item.name or item.id,
                "language_id": item.value["language_id"],
                "language": item.value.get("language"),
                "dialect": item.value.get("dialect"),
                "ecosystem": item.value.get("ecosystem"),
                "frameworks": item.value.get("frameworks", []),
                "detection": item.value.get("language_detection", {}),
            }
        )
    return values


def message_language_metadata(payload: Any) -> list[dict[str, Any]]:
    registry = language_registry()
    extras = getattr(payload, "model_extra", None) or {}
    message = str(getattr(payload, "message", "") or "")
    explicit = _first_string(extras.get("language_id"), extras.get("languageId"), extras.get("language"))
    path = _first_string(
        extras.get("path"), extras.get("file_name"), extras.get("fileName"),
        extras.get("uri"), extras.get("current_file"), extras.get("currentFile"),
    )
    detected: list[dict[str, Any]] = []
    workspace_detected = [
        str(item.value.get("language_id"))
        for item in getattr(payload, "context", [])
        if hasattr(item, "value") and isinstance(item.value, dict) and item.value.get("language_id")
    ] if not isinstance(getattr(payload, "context", ""), str) else []
    biases = _payload_biases(payload, workspace_detected)
    if explicit or path:
        result = registry.detect(
            explicit_language_id=explicit or None,
            path=path or None,
            content=message,
            workspace_languages=biases["workspace"],
            session_languages=biases["session"],
            workspace_history_languages=biases["workspace_history"],
            global_history_languages=biases["global_history"],
        )
        detected.append({"scope": "message", **result.public_dict()})
    for segment in _message_code_segments(message):
        result = registry.detect(
            explicit_language_id=segment["declared_language"],
            content=segment["content"],
            workspace_languages=biases["workspace"],
            session_languages=biases["session"],
            workspace_history_languages=biases["workspace_history"],
            global_history_languages=biases["global_history"],
        )
        if result.language_id == "plaintext":
            continue
        detected.append(
            {
                "scope": segment["scope"],
                "segment": segment["segment"],
                "start": segment["start"],
                "end": segment["end"],
                **result.public_dict(),
            }
        )
    unique: dict[tuple[str, str, int], dict[str, Any]] = {}
    for item in detected:
        unique[(item["scope"], item["language_id"], int(item.get("segment", -1)))] = item
    return list(unique.values())
