from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse

import backend.app as api_module
from backend.project_store import ProjectStoreError


def _replace_list_projects(
    replacement: Callable[[], list[dict[str, Any]]],
) -> Callable[[], list[dict[str, Any]]]:
    original = api_module.project_store.list_projects
    api_module.project_store.list_projects = replacement  # type: ignore[method-assign]
    return original


def main() -> None:
    now = datetime(2026, 7, 24, 3, 15, tzinfo=timezone.utc)
    original = _replace_list_projects(
        lambda: [
            {
                "project_id": "h5vision/protoFastAPI",
                "display_name": "protoFastAPI",
                "current_snapshot_id": "snap_01",
                "git_commit_sha": "4ea031ecb0f8f503e3d8ef27b01e53d771ab1234",
                "git_branch": "main",
                "git_dirty": False,
                "git_committed_at": now,
                "index_status": "completed",
                "index_completed_at": now,
                "updated_at": now,
            }
        ]
    )
    try:
        request = Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/v1/IngestResponse",
                "raw_path": b"/v1/IngestResponse",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 50000),
                "server": ("testserver", 80),
            }
        )
        request.state.request_id = "req_contract_test"
        response = Response()
        payload = api_module.list_indexed_projects(request, response)
        assert not isinstance(payload, JSONResponse)
        assert response.headers["cache-control"] == "no-store"
        serialized = payload.model_dump(mode="json")
        assert serialized["schema_version"] == "1.0"
        assert serialized["total"] == 1
        assert serialized["projects"][0]["project_id"] == "h5vision/protoFastAPI"
        assert serialized["projects"][0]["git_short_sha"] == "4ea031e"
        assert serialized["projects"][0]["index_status"] == "ready"

        api_module.project_store.list_projects = lambda: []
        empty = api_module.list_indexed_projects(request, Response())
        assert not isinstance(empty, JSONResponse)
        assert empty.projects == []
        assert empty.total == 0

        def unavailable() -> list[dict[str, Any]]:
            raise ProjectStoreError("unavailable")

        api_module.project_store.list_projects = unavailable
        error_response = api_module.list_indexed_projects(request, Response())
        assert isinstance(error_response, JSONResponse)
        assert error_response.status_code == 503
        error_payload = json.loads(error_response.body)
        assert error_payload["error"]["code"] == "PROJECT_REGISTRY_UNAVAILABLE"
        assert error_payload["error"]["retryable"] is True
    finally:
        api_module.project_store.list_projects = original  # type: ignore[method-assign]

    print("IngestResponse contract verification passed")


if __name__ == "__main__":
    main()
