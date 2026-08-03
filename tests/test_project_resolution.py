from __future__ import annotations

import unittest

from backend.project_resolution import (
    parse_project_id_aliases,
    resolve_project_id,
)


class ProjectResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            {
                "project_id": "default",
                "display_name": "default",
                "index_status": "ready",
                "current_snapshot_id": None,
            },
            {
                "project_id": "h5vision/fest-api",
                "display_name": "fest-api",
                "index_status": "completed",
                "current_snapshot_id": "snap_123",
            },
        ]

    def test_exact_project_id_is_preserved(self) -> None:
        resolved = resolve_project_id("h5vision/fest-api", self.rows)

        self.assertEqual(resolved.resolved_project_id, "h5vision/fest-api")
        self.assertEqual(resolved.strategy, "exact_project_id")
        self.assertEqual(resolved.confidence, 1.0)

    def test_git_url_resolves_to_canonical_project_id(self) -> None:
        resolved = resolve_project_id(
            "https://github.com/h5vision/fest-api.git",
            self.rows,
        )

        self.assertEqual(resolved.resolved_project_id, "h5vision/fest-api")
        self.assertEqual(resolved.strategy, "normalized_project_id")

    def test_repository_name_resolves_as_project_alias(self) -> None:
        resolved = resolve_project_id("fest-api", self.rows)

        self.assertEqual(resolved.resolved_project_id, "h5vision/fest-api")
        self.assertEqual(resolved.strategy, "project_alias")

    def test_frontend_fastapi_typo_resolves_by_unique_fuzzy_match(self) -> None:
        resolved = resolve_project_id("FastAPI", self.rows)

        self.assertEqual(resolved.resolved_project_id, "h5vision/fest-api")
        self.assertEqual(resolved.strategy, "fuzzy_project_alias")
        self.assertGreaterEqual(resolved.confidence, 0.78)

    def test_configured_alias_has_priority_over_fallback(self) -> None:
        resolved = resolve_project_id(
            "LegacyWorkspace",
            self.rows,
            configured_aliases=(
                ("LegacyWorkspace", "h5vision/fest-api"),
            ),
        )

        self.assertEqual(resolved.resolved_project_id, "h5vision/fest-api")
        self.assertEqual(resolved.strategy, "configured_alias")

    def test_unknown_id_uses_only_non_default_indexed_project(self) -> None:
        resolved = resolve_project_id("Vision", self.rows)

        self.assertEqual(resolved.resolved_project_id, "h5vision/fest-api")
        self.assertEqual(resolved.strategy, "sole_indexed_project")

    def test_ambiguous_repository_name_is_not_guessed(self) -> None:
        rows = self.rows + [
            {
                "project_id": "other/fest-api",
                "display_name": "fest-api",
                "index_status": "completed",
                "current_snapshot_id": "snap_456",
            }
        ]

        resolved = resolve_project_id("fest-api", rows)

        self.assertIsNone(resolved.resolved_project_id)
        self.assertEqual(resolved.strategy, "ambiguous_project_alias")
        self.assertEqual(
            set(resolved.candidates),
            {"h5vision/fest-api", "other/fest-api"},
        )

    def test_multiple_unrelated_projects_do_not_cross_search(self) -> None:
        rows = self.rows + [
            {
                "project_id": "other/backend",
                "display_name": "backend",
                "index_status": "completed",
                "current_snapshot_id": "snap_789",
            }
        ]

        resolved = resolve_project_id("UnknownWorkspace", rows)

        self.assertIsNone(resolved.resolved_project_id)
        self.assertEqual(resolved.strategy, "unresolved_project_id")

    def test_alias_parser_accepts_multiple_separators(self) -> None:
        aliases = parse_project_id_aliases(
            "FastAPI=h5vision/fest-api;Vision=h5vision/vision\n"
            "Legacy=other/repo"
        )

        self.assertEqual(
            aliases,
            (
                ("FastAPI", "h5vision/fest-api"),
                ("Vision", "h5vision/vision"),
                ("Legacy", "other/repo"),
            ),
        )


if __name__ == "__main__":
    unittest.main()
