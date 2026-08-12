from __future__ import annotations

import importlib
from pathlib import Path

from backend.contracts.admin import FrontendClientWriteRequest, NetworkEndpointSettings
from backend.contracts.models import ModelListResponse
from backend.contracts.projects import ProjectVersionCheckRequest
from backend.contracts.repositories import UploadCreateRequest
from backend.domains.chat.schemas import ChatRequest
from backend.schemas import (
    ChatRequest as LegacyChatRequest,
    ModelListResponse as LegacyModelListResponse,
    ProjectVersionCheckRequest as LegacyProjectVersionCheckRequest,
    UploadCreateRequest as LegacyUploadCreateRequest,
)


ROOT = Path(__file__).resolve().parents[1]


def test_legacy_facade_reexports_canonical_schema_objects() -> None:
    assert LegacyChatRequest is ChatRequest
    assert LegacyModelListResponse is ModelListResponse
    assert LegacyProjectVersionCheckRequest is ProjectVersionCheckRequest
    assert LegacyUploadCreateRequest is UploadCreateRequest


def test_canonical_schema_modules_do_not_depend_on_legacy_facade() -> None:
    modules = (
        "backend/contracts/common.py",
        "backend/contracts/models.py",
        "backend/contracts/projects.py",
        "backend/contracts/providers.py",
        "backend/contracts/repositories.py",
        "backend/contracts/vector.py",
        "backend/domains/chat/schemas.py",
    )

    for relative_path in modules:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "backend.schemas" not in source
        assert "from ...schemas import" not in source


def test_chat_request_normalization_uses_the_same_canonical_class() -> None:
    payload = LegacyChatRequest.model_validate(
        {
            "role": "user",
            "content": "현재 열린 프로젝트를 요약해줘",
            "history": [{"role": "assistant", "content": "이전 답변"}],
        }
    )

    assert type(payload) is ChatRequest
    assert payload.message == "현재 열린 프로젝트를 요약해줘"
    assert payload.history[0].content == "이전 답변"


def test_canonical_modules_are_importable_without_legacy_facade_imports() -> None:
    for module_name in (
        "backend.contracts.models",
        "backend.contracts.projects",
        "backend.contracts.repositories",
        "backend.domains.chat.schemas",
    ):
        assert importlib.import_module(module_name) is not None


def test_admin_ip_contracts_use_the_runtime_validator_after_schema_split() -> None:
    client = FrontendClientWriteRequest(
        name="VS Code",
        ip="192.168.0.18",
        port=8888,
    )
    endpoint = NetworkEndpointSettings(ip="192.168.0.12", port=11500)

    assert client.ip == "192.168.0.18"
    assert endpoint.ip == "192.168.0.12"
