from __future__ import annotations

import hashlib
import json

from pydantic import ValidationError

from backend.app import app
from backend.schemas import ChatRequest, RuntimeVectorSettingsWrite


EXPECTED_OPENAPI_SHA256 = (
    "61085a39c88bd48e1c5e8ada5b70bad1760801b9d473cd6247933400c336d272"
)


def main() -> None:
    canonical = ChatRequest.model_validate(
        {
            "schema_version": "1.0",
            "client_request_id": "contract-test-001",
            "project_id": "Vision",
            "session_id": "Vision",
            "model_id": "backendai-default",
            "message": "contract test",
            "top_k": 5,
            "history": [],
            "stream": False,
        }
    )
    assert canonical.message == "contract test"
    assert ChatRequest.model_validate(
        {
            "prompt": "legacy alias",
            "project_id": "Vision",
            "session_id": "Vision",
            "history": [],
        }
    ).message == "legacy alias"
    blank_optional = ChatRequest.model_validate(
        {
            "message": "blank optional fields",
            "project_id": "Vision",
            "session_id": "Vision",
            "client_request_id": "   ",
            "model_id": "  ",
            "top_k": None,
            "history": [],
            "stream": None,
            "schema_version": None,
        }
    )
    assert blank_optional.client_request_id is None
    assert blank_optional.model_id is None
    assert blank_optional.top_k is None
    assert blank_optional.stream is None
    assert blank_optional.schema_version is None
    frontend_request = ChatRequest.model_validate(
        {
            "project_id": "FastAPI",
            "message": "current Team-vision request",
            "session_id": "9efda536-b502-49e4-926d-53343a428df0",
            "context": [
                {
                    "id": "file:///c%3A/project/README.md",
                    "name": "README.md",
                    "value": {
                        "$mid": 1,
                        "fsPath": "c:\\project\\README.md",
                        "path": "/c:/project/README.md",
                        "scheme": "file",
                    },
                },
                {
                    "id": "vscode.customizations.index",
                    "name": "prompt:customizationsIndex",
                    "toolReferences": [{"name": "copilot_readFile"}],
                    "modelDescription": "Chat customizations index",
                },
            ],
        }
    )
    assert frontend_request.history == []
    assert frontend_request.top_k is None
    assert len(frontend_request.context) == 2
    assert frontend_request.context[0].name == "README.md"
    string_context_request = ChatRequest.model_validate(
        {
            "project_id": "FastAPI",
            "message": "string context request",
            "session_id": "5e442c7d-dd26-4d85-9540-69daa727494d",
            "context": "파일: README.md\n\n# Project\n문서 본문",
            "history": [],
            "top_k": 3,
        }
    )
    assert string_context_request.context.endswith("문서 본문")

    for invalid in (
        {"message": "missing required fields"},
        {
            "message": "streaming",
            "project_id": "Vision",
            "session_id": "Vision",
            "history": [],
            "stream": True,
        },
        {
            "message": "extra",
            "project_id": "Vision",
            "session_id": "Vision",
            "history": [],
            "unexpected": True,
        },
    ):
        try:
            ChatRequest.model_validate(invalid)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"Invalid chat request was accepted: {invalid}")

    spec = app.openapi()
    canonical_spec = json.dumps(
        spec,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    actual_hash = hashlib.sha256(canonical_spec).hexdigest()
    assert actual_hash == EXPECTED_OPENAPI_SHA256, (
        "OpenAPI contract changed. Review the diff in the API freeze meeting and "
        f"update EXPECTED_OPENAPI_SHA256 intentionally: {actual_hash}"
    )
    operation = spec["paths"]["/v1/chat"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]
    context_schema = spec["components"]["schemas"]["ChatRequest"]["properties"][
        "context"
    ]
    assert {item.get("type") for item in context_schema["anyOf"]} == {
        "string",
        "array",
    }
    for status_code in ("422", "500", "502", "503", "504"):
        assert (
            operation["responses"][status_code]["content"]["application/json"][
                "schema"
            ]["$ref"]
            == "#/components/schemas/ErrorResponse"
        )
    assert "429" in operation["responses"]
    assert "/v1/models" in spec["paths"]
    assert "/v1/IngestResponse" in spec["paths"]
    assert "/v1/repositories" in spec["paths"]
    assert "/v1/repositories/{source_id}/tree" in spec["paths"]
    assert "/v1/projects/{project_id}/version/check" in spec["paths"]
    assert "/v1/admin/repository-sources" in spec["paths"]
    assert "/v1/admin/repository-sources/{source_id}/index" in spec["paths"]
    assert "/v1/admin/indexing-jobs/{job_id}/resume" in spec["paths"]
    assert "/v1/admin/models" in spec["paths"]
    assert "/v1/admin/models/access" in spec["paths"]
    assert "/v1/projects/{project_id}/tree" in spec["paths"]
    assert "/v1/projects/{project_id}/file" in spec["paths"]
    assert "/v1/projects/{project_id}/index-validation" in spec["paths"]
    assert "/v1/indexing-jobs" in spec["paths"]

    request_schema = spec["components"]["schemas"]["ChatRequest"]
    response_schema = spec["components"]["schemas"]["ChatResponse"]
    assert {"project_id", "message", "session_id"}.issubset(
        request_schema["required"]
    )
    assert "history" not in request_schema["required"]
    assert request_schema["additionalProperties"] is False
    assert {"answer", "source", "metadata"} == set(response_schema["required"])
    source_schema = spec["components"]["schemas"]["SourceDocument"]
    assert {"file", "chunk"}.issubset(source_schema["required"])
    model_schema = spec["components"]["schemas"]["ModelInfo"]
    assert {
        "model_name",
        "deployment_type",
        "enabled",
    }.issubset(model_schema["required"])
    assert "endpoint" in model_schema["properties"]
    vector_settings_schema = RuntimeVectorSettingsWrite.model_json_schema()
    assert {
        "embedding_deployment",
        "embedding_provider",
        "embedding_base_url",
        "embedding_model",
        "embedding_model_id",
        "embedding_dimension",
        "embedding_batch_size",
    }.issubset(vector_settings_schema["required"])
    assert set(
        model_schema["properties"]["deployment_type"]["enum"]
    ) == {"cloud", "local", "remote_server"}
    assert model_schema["properties"]["provider"]["type"] == "string"
    indexing_job_schema = spec["components"]["schemas"]["IndexingJobSummary"]
    assert {
        "job_id",
        "job_kind",
        "project_id",
        "state",
        "stage",
        "active",
        "progress_percent",
        "status_url",
    }.issubset(indexing_job_schema["required"])
    assert "stalled" in indexing_job_schema["properties"]
    version_response_schema = spec["components"]["schemas"][
        "ProjectVersionCheckResponse"
    ]
    version_request_schema = spec["components"]["schemas"][
        "ProjectVersionCheckRequest"
    ]
    assert "tree" in version_request_schema["properties"]
    assert {
        "project_id",
        "backend_registered",
        "backend_source",
        "same_version",
        "relation",
        "client",
        "checks",
    }.issubset(version_response_schema["required"])
    project_list_operation = spec["paths"]["/v1/IngestResponse"]["get"]
    assert (
        project_list_operation["responses"]["200"]["content"]["application/json"][
            "schema"
        ]["$ref"]
        == "#/components/schemas/IndexedProjectListResponse"
    )
    project_list_schema = spec["components"]["schemas"][
        "IndexedProjectListResponse"
    ]
    project_item_schema = spec["components"]["schemas"]["IndexedProjectItem"]
    assert {
        "request_id",
        "projects",
        "total",
        "generated_at",
    }.issubset(project_list_schema["required"])
    assert {
        "project_id",
        "project_name",
        "index_status",
    }.issubset(project_item_schema["required"])
    repository_list_schema = spec["components"]["schemas"][
        "RepositoryBrowserListResponse"
    ]
    repository_item_schema = spec["components"]["schemas"][
        "RepositoryBrowserItem"
    ]
    repository_tree_schema = spec["components"]["schemas"][
        "RepositorySourceTreeResponse"
    ]
    assert {"repositories", "total", "generated_at"}.issubset(
        repository_list_schema["required"]
    )
    assert {
        "source_id",
        "project_id",
        "source_available",
        "index_status",
        "version_status",
        "source_tree_url",
        "indexed_tree_url",
    }.issubset(repository_item_schema["required"])
    assert {
        "source_id",
        "project_id",
        "revision",
        "entries",
        "total",
    }.issubset(repository_tree_schema["required"])

    print(
        json.dumps(
            {
                "status": "ok",
                "schema_version": "1.0",
                "openapi_sha256": actual_hash,
                "chat_required": request_schema["required"],
                "documented_errors": ["422", "429", "500", "502", "503", "504"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
