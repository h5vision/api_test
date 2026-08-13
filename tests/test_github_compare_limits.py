from __future__ import annotations

import urllib.error
from email.message import Message

import pytest

from backend.snapshots.adapters.github import GithubAdapterError, PublicGithubAdapter


BASE_SHA = "a" * 40
TARGET_SHA = "b" * 40
MERGE_BASE_SHA = "c" * 40


def test_compare_marks_exact_github_file_limit_as_incomplete(monkeypatch) -> None:
    adapter = PublicGithubAdapter()
    response = {
        "status": "ahead",
        "ahead_by": 1,
        "behind_by": 0,
        "total_commits": 1,
        "base_commit": {"sha": BASE_SHA},
        "head_commit": {"sha": TARGET_SHA},
        "merge_base_commit": {"sha": MERGE_BASE_SHA},
        "files": [
            {
                "filename": f"src/file_{index}.py",
                "status": "modified",
                "sha": f"{index:040x}",
                "additions": 1,
                "deletions": 0,
                "changes": 1,
                "patch": "@@ -1 +1 @@",
            }
            for index in range(300)
        ],
    }
    monkeypatch.setattr(adapter, "_request_json", lambda path: response)

    result = adapter.compare_commits("h5vision/api_test", BASE_SHA, TARGET_SHA)

    assert result["file_count"] == 300
    assert len(result["files"]) == 300
    assert result["truncated"] is True
    assert result["reason"] == "github_compare_file_limit"


def test_compare_reports_returned_file_count_when_complete(monkeypatch) -> None:
    adapter = PublicGithubAdapter()
    response = {
        "status": "ahead",
        "ahead_by": 1,
        "behind_by": 0,
        "total_commits": 1,
        "base_commit": {"sha": BASE_SHA},
        "head_commit": {"sha": TARGET_SHA},
        "merge_base_commit": {"sha": MERGE_BASE_SHA},
        "files": [
            {
                "filename": "src/app.py",
                "status": "modified",
                "sha": "d" * 40,
                "changes": 1,
            }
        ],
    }
    monkeypatch.setattr(adapter, "_request_json", lambda path: response)

    result = adapter.compare_commits("h5vision/api_test", BASE_SHA, TARGET_SHA)

    assert result["file_count"] == 1
    assert result["truncated"] is False
    assert result["reason"] is None


def test_github_rate_limit_has_a_distinct_status(monkeypatch) -> None:
    headers = Message()
    headers["X-RateLimit-Remaining"] = "0"

    def rate_limited(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://api.github.com/repos/h5vision/api_test",
            403,
            "rate limited",
            headers,
            None,
        )

    monkeypatch.setattr("urllib.request.urlopen", rate_limited)

    with pytest.raises(GithubAdapterError) as captured:
        PublicGithubAdapter().get_repository("h5vision/api_test")

    assert captured.value.status_code == 429
