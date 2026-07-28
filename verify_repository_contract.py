from __future__ import annotations

from datetime import datetime, timezone

from pydantic import ValidationError

import backend.app as api_module
from backend.schemas import RepositorySourceWriteRequest


def main() -> None:
    source = RepositorySourceWriteRequest.model_validate(
        {
            "source_id": "git-github-h5vision-fest-api",
            "project_id": "h5vision/fest-api",
            "source_type": "git",
            "root_relative_path": "fest-api",
            "repository_url": "https://github.com/h5vision/fest-api.git",
            "default_branch": "main",
            "enabled": True,
        }
    )
    assert source.root_relative_path == "fest-api"
    for unsafe in ("../fest-api", "C:/fest-api", "/fest-api"):
        try:
            RepositorySourceWriteRequest.model_validate(
                {
                    **source.model_dump(),
                    "root_relative_path": unsafe,
                }
            )
        except ValidationError:
            pass
        else:
            raise AssertionError(f"Unsafe repository path was accepted: {unsafe}")

    active = {
        "project_id": "h5vision/fest-api",
        "snapshot_id": "snap_test",
        "generation_id": "gen_test",
        "revision": "afe41126f624af30038cc8e17b2aaf60ebd4b838",
        "manifest_sha256": "0" * 64,
    }
    original_list_tree = api_module.repository_store.list_tree
    original_get_file = api_module.repository_store.get_file
    original_active = api_module.repository_store.get_active_generation
    original_pg_count = api_module.repository_store.generation_chunk_count
    original_vector_count = api_module.vector_store.count_generation
    try:
        api_module.repository_store.list_tree = lambda project_id, prefix="": (  # type: ignore[method-assign]
            active,
            [
                {
                    "path": "fastapi/applications.py",
                    "name": "applications.py",
                    "entry_type": "file",
                    "language": "python",
                    "size_bytes": 123,
                    "content_sha256": "1" * 64,
                    "indexable": True,
                }
            ],
        )
        api_module.repository_store.get_file = lambda project_id, path: (  # type: ignore[method-assign]
            active,
            {
                "path": path,
                "language": "python",
                "size_bytes": 20,
                "content_sha256": "2" * 64,
                "content": "from fastapi import FastAPI",
            },
        )
        api_module.repository_store.get_active_generation = lambda project_id: active  # type: ignore[method-assign]
        api_module.repository_store.generation_chunk_count = lambda generation_id: 12  # type: ignore[method-assign]
        api_module.vector_store.count_generation = lambda project_id, generation_id: 12  # type: ignore[method-assign]

        tree = api_module.get_project_tree("h5vision/fest-api", "")
        assert tree.total == 1
        assert tree.entries[0].path == "fastapi/applications.py"
        assert tree.generation_id == "gen_test"

        file_response = api_module.get_project_file(
            "h5vision/fest-api", "fastapi/applications.py"
        )
        assert file_response.content.startswith("from fastapi")
        assert file_response.snapshot_id == "snap_test"

        validation = api_module.validate_project_index("h5vision/fest-api")
        assert validation.consistent is True
        assert validation.checked_at <= datetime.now(timezone.utc)
    finally:
        api_module.repository_store.list_tree = original_list_tree  # type: ignore[method-assign]
        api_module.repository_store.get_file = original_get_file  # type: ignore[method-assign]
        api_module.repository_store.get_active_generation = original_active  # type: ignore[method-assign]
        api_module.repository_store.generation_chunk_count = original_pg_count  # type: ignore[method-assign]
        api_module.vector_store.count_generation = original_vector_count  # type: ignore[method-assign]

    spec = api_module.app.openapi()
    assert "/v1/repositories" in spec["paths"]
    assert "/v1/repositories/{source_id}/tree" in spec["paths"]
    assert "/v1/projects/{project_id}/tree" in spec["paths"]
    assert "/v1/projects/{project_id}/file" in spec["paths"]
    assert "/v1/projects/{project_id}/index-validation" in spec["paths"]
    assert "/v1/admin/repository-sources" in spec["paths"]
    assert "/v1/admin/repository-sources/{source_id}/index" in spec["paths"]
    assert "/v1/admin/indexing-jobs/{job_id}/resume" in spec["paths"]
    print(
        "Repository browser, source tree, snapshot tree, file and generation "
        "contract verification passed"
    )


if __name__ == "__main__":
    main()
