from __future__ import annotations

from backend.chat_intake import normalize_chat_intake
from backend.language_registry import language_registry
from backend.schemas import ChatRequest


def test_registry_uses_vscode_filename_extension_and_first_line_precedence() -> None:
    registry = language_registry()

    dockerfile = registry.detect(file_name="Dockerfile", content="FROM python:3.12")
    tsx = registry.detect(file_name="Component.tsx", content="export const App = () => <main />")
    shell = registry.detect(file_name="untitled", content="#!/usr/bin/env bash\necho ok")

    assert dockerfile.language_id == "dockerfile"
    assert dockerfile.source == "filename"
    assert tsx.language_id == "typescriptreact"
    assert tsx.dialect == "tsx"
    assert tsx.ecosystem == "javascript"
    assert shell.language_id == "shellscript"
    assert shell.source == "first_line"


def test_explicit_language_id_wins_and_unknown_extension_ids_are_preserved() -> None:
    registry = language_registry()

    explicit = registry.detect(
        explicit_language_id="python",
        file_name="looks-like.ts",
        content="const value = 1",
    )
    contributed = registry.detect(
        explicit_language_id="vendor-special-language",
        file_name="unknown.vendor",
    )

    assert explicit.language_id == "python"
    assert explicit.source == "explicit"
    assert contributed.language_id == "vendor-special-language"
    assert contributed.source == "explicit"


def test_extensionless_content_uses_high_confidence_fallback() -> None:
    detected = language_registry().detect(
        file_name="untitled",
        content="def greet(name: str) -> str:\n    return f'Hello {name}'",
    )

    assert detected.language_id == "python"
    assert detected.source == "content"


def test_workspace_and_session_bias_reorder_ambiguous_content_candidates() -> None:
    registry = language_registry()
    code = "const user = getUser();\nconsole.log(user);"

    isolated = registry.detect(file_name="untitled", content=code)
    contextual = registry.detect(
        file_name="untitled",
        content=code,
        workspace_languages=["typescript"],
        session_languages=["typescript"],
    )

    assert isolated.language_id == "javascript"
    assert contextual.language_id == "typescript"
    assert "workspace_bias" in contextual.evidence_sources
    assert "session_bias" in contextual.evidence_sources
    assert {candidate["language_id"] for candidate in contextual.candidates} >= {
        "javascript",
        "typescript",
    }


def test_chat_context_language_normalization_is_on_when_deep_interpretation_is_off() -> None:
    request = ChatRequest.model_validate(
        {
            "role": "user",
            "content": "이 첨부 코드를 설명해줘",
            "context": [
                {
                    "id": "file:///workspace/src/App.tsx",
                    "name": "App.tsx",
                    "value": {
                        "kind": "text",
                        "file_name": "App.tsx",
                        "content": "export function App() { return <main>Hello</main>; }",
                    },
                }
            ],
        }
    )

    result = normalize_chat_intake(request, deep_enabled=False)
    item = result.payload.context[0]

    assert item.value["language_id"] == "typescriptreact"
    assert item.value["language"] == "typescript"
    assert item.value["dialect"] == "tsx"
    assert item.value["language_detection"]["source"] == "extension"
    assert result.metadata()["context_languages"][0]["language_id"] == "typescriptreact"


def test_client_language_id_has_priority_during_chat_normalization() -> None:
    request = ChatRequest.model_validate(
        {
            "content": "검토해줘",
            "context": [
                {
                    "name": "template.txt",
                    "value": {
                        "kind": "text",
                        "file_name": "template.txt",
                        "languageId": "handlebars",
                        "content": "<h1>{{ title }}</h1>",
                    },
                }
            ],
        }
    )

    result = normalize_chat_intake(request, deep_enabled=False)

    assert result.payload.context[0].value["language_id"] == "handlebars"
    assert result.payload.context[0].value["language_detection"]["source"] == "explicit"


def test_declared_markdown_fence_is_visible_in_intake_normalization_metadata() -> None:
    request = ChatRequest.model_validate(
        {
            "content": "이 코드를 설명해줘\n```tsx\nexport const App = () => <main />\n```",
        }
    )

    metadata = normalize_chat_intake(request, deep_enabled=False).metadata()

    assert metadata["message_languages"][0]["language_id"] == "typescriptreact"
    assert metadata["message_languages"][0]["scope"] == "code_fence"


def test_unlabeled_pasted_code_is_split_from_natural_language_and_detected() -> None:
    request = ChatRequest.model_validate(
        {
            "content": (
                "이 함수가 왜 실패하는지 알려줘\n\n"
                "async function getUser(id: string) {\n"
                "  const response = await fetch(`/users/${id}`);\n"
                "  return response.json();\n"
                "}"
            ),
            "workspace_languages": ["typescript"],
        }
    )

    metadata = normalize_chat_intake(request, deep_enabled=False).metadata()
    pasted = next(item for item in metadata["message_languages"] if item["scope"] == "pasted_code")

    assert pasted["language_id"] == "typescript"
    assert pasted["start"] > 0
    assert "content" in pasted["evidence_sources"]
