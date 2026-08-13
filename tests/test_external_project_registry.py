from __future__ import annotations

from typing import Any

from backend.domains.external_projects.service import ExternalProjectRegistryService


class FakeRagClient:
    base_url = "http://rag-lab:8200"

    def __init__(self, projects: list[dict[str, Any]], *, fail: bool = False) -> None:
        self._projects = projects
        self._fail = fail

    def health(self) -> dict[str, Any]:
        if self._fail:
            raise RuntimeError("offline")
        return {"ok": True}

    def projects(self) -> list[dict[str, Any]]:
        if self._fail:
            raise RuntimeError("offline")
        return list(self._projects)


class FakeRegistry:
    def __init__(self, vision_projects: list[dict[str, Any]]) -> None:
        self.vision_projects = vision_projects
        self.catalog: dict[str, dict[str, Any]] = {}
        self.bindings: dict[str, dict[str, Any]] = {}
        self.target: dict[str, Any] = {}

    def upsert_target(self, **kwargs: Any) -> dict[str, Any]:
        self.target.update(kwargs)
        return dict(self.target)

    def mark_target_offline(self, target_id: str, error: str) -> None:
        self.target.update(target_id=target_id, availability="offline", error=error)
        for item in self.catalog.values():
            item["availability"] = "stale"

    def begin_observation(self, target_id: str) -> None:
        for item in self.catalog.values():
            if item["target_id"] == target_id:
                item["availability"] = "stale"

    def upsert_external_project(self, *, target_id: str, value: dict[str, Any]) -> dict[str, Any]:
        project_id = str(value["project_id"])
        item = {
            "target_id": target_id,
            "external_project_id": project_id,
            "revision": value.get("commit"),
            "availability": "online",
        }
        self.catalog[project_id] = item
        return item

    def list_external_projects(self, target_id: str) -> list[dict[str, Any]]:
        return [dict(item) for item in self.catalog.values() if item["target_id"] == target_id]

    def list_vision_projects(self) -> list[dict[str, Any]]:
        return list(self.vision_projects)

    def list_bindings(self, target_id: str) -> list[dict[str, Any]]:
        return [dict(item) for item in self.bindings.values() if item["target_id"] == target_id]

    def upsert_binding(self, **kwargs: Any) -> dict[str, Any]:
        current = self.bindings.get(kwargs["project_id"])
        if kwargs.get("preserve_manual") and current and current.get("binding_method") == "manual":
            return current
        self.bindings[kwargs["project_id"]] = dict(kwargs)
        return dict(kwargs)


def test_revision_exact_binding_is_verified() -> None:
    registry = FakeRegistry([
        {
            "project_id": "h5vision/fest-api",
            "snapshot_revision": "a" * 40,
        }
    ])
    service = ExternalProjectRegistryService(
        registry,
        FakeRagClient([{"project_id": "fest-api", "commit": "a" * 40}]),
    )

    report = service.sync()

    assert report.verified_bindings == 1
    binding = registry.bindings["h5vision/fest-api"]
    assert binding["external_project_id"] == "fest-api"
    assert binding["binding_method"] == "revision_exact"
    assert binding["verification_state"] == "verified"


def test_unique_leaf_match_is_candidate_not_verified() -> None:
    registry = FakeRegistry([{"project_id": "h5vision/fest-api"}])
    service = ExternalProjectRegistryService(
        registry,
        FakeRagClient([{"project_id": "fest-api", "commit": "b" * 40}]),
    )

    report = service.sync()

    assert report.candidate_bindings == 1
    assert registry.bindings["h5vision/fest-api"]["verification_state"] == "candidate"


def test_ambiguous_leaf_is_not_bound() -> None:
    registry = FakeRegistry([{"project_id": "h5vision/api"}])
    service = ExternalProjectRegistryService(
        registry,
        FakeRagClient([
            {"project_id": "team-a/api", "commit": "a" * 40},
            {"project_id": "team-b/api", "commit": "b" * 40},
        ]),
    )

    report = service.sync()

    assert report.ambiguous_projects == 1
    assert "h5vision/api" not in registry.bindings


def test_missing_external_project_is_stale_not_deleted() -> None:
    registry = FakeRegistry([])
    registry.catalog["old"] = {
        "target_id": "rag-lab-main",
        "external_project_id": "old",
        "revision": "a" * 40,
        "availability": "online",
    }
    service = ExternalProjectRegistryService(registry, FakeRagClient([]))

    report = service.sync()

    assert report.stale_projects == 1
    assert registry.catalog["old"]["availability"] == "stale"


def test_offline_rag_marks_catalog_stale() -> None:
    registry = FakeRegistry([])
    registry.catalog["old"] = {
        "target_id": "rag-lab-main",
        "external_project_id": "old",
        "availability": "online",
    }
    service = ExternalProjectRegistryService(registry, FakeRagClient([], fail=True))

    report = service.sync()

    assert report.availability == "offline"
    assert registry.catalog["old"]["availability"] == "stale"
