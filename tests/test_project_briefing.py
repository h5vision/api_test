from __future__ import annotations

from backend.briefings import build_project_briefing_response
from backend.rag_lab import RagLabProjectBinding


class _BriefingClient:
    def resolve_project(self, project_id: str) -> RagLabProjectBinding:
        return RagLabProjectBinding(
            requested_project_id=project_id,
            external_project_id="fastapi-cli",
            binding_strength="project_only",
            verification_state="unverified",
            revision="fb2dd86bf442489f7a01a5ab0509516691dc2d58",
            indexed_at="2026-08-10T16:05:36+09:00",
            fingerprint={},
        )

    def briefing(self, project_id: str) -> dict:
        assert project_id == "fastapi-cli"
        return {
            "project_id": project_id,
            "briefing": "## 이 프로젝트는\nFastAPI CLI입니다.",
            "references": [{"n": 3, "path": "src/fastapi_cli/cli.py"}],
            "reference_files": [{"path": "src/fastapi_cli/cli.py"}],
            "mentioned_files": [],
            "structure": {"total_files": 92},
            "commit": "fb2dd86bf442489f7a01a5ab0509516691dc2d58",
            "index_commit": "fb2dd86bf442489f7a01a5ab0509516691dc2d58",
            "generated_at": "2026-08-10T07:05:50+00:00",
            "outdated": False,
            "ok": True,
            "md_path": "C:\\Pj\\rag_lab\\data\\briefings\\fastapi-cli.md",
            "briefing_tokens": 292,
        }


def test_briefing_projection_maps_external_project_and_revision() -> None:
    client = _BriefingClient()
    binding = client.resolve_project("h5vision/fastapi-cli")
    response = build_project_briefing_response(
        "h5vision/fastapi-cli",
        "fb2dd86bf442",
        binding,
        client.briefing(binding.external_project_id),
    )

    assert response.project_id == "h5vision/fastapi-cli"
    assert response.external_project_id == "fastapi-cli"
    assert response.revision_status == "same"
    assert response.references[0]["path"] == "src/fastapi_cli/cli.py"
    assert response.metadata["briefing_tokens"] == 292
    assert "md_path" not in response.model_dump(mode="json")


def test_briefing_projection_marks_different_frontend_commit() -> None:
    client = _BriefingClient()
    binding = client.resolve_project("fastapi-cli")
    response = build_project_briefing_response(
        "fastapi-cli",
        "aaaaaaaaaaaa",
        binding,
        client.briefing(binding.external_project_id),
    )

    assert response.revision_status == "different"
