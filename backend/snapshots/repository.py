from __future__ import annotations


from datetime import datetime
from typing import Any


import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


from ..config import Settings




class SnapshotRepositoryError(RuntimeError):
    pass


class SnapshotIntegrityError(SnapshotRepositoryError):
    """The durable record conflicts with the verified GitHub identity."""




class PostgresGithubSnapshotRepository:
    """PostgreSQL persistence for the public GitHub Commit Snapshot MVP.


    The schema is created only by the explicit migration. This class never runs
    schema-changing DDL at application startup.
    """


    def __init__(self, settings: Settings) -> None:
        self._settings = settings


    @property
    def configured(self) -> bool:
        return bool(self._settings.postgres_password)


    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        if not self.configured:
            raise SnapshotRepositoryError(
                "PostgreSQL is required for the GitHub Snapshot MVP"
            )
        return psycopg.connect(
            host=self._settings.postgres_host,
            port=self._settings.postgres_port,
            dbname=self._settings.postgres_db,
            user=self._settings.postgres_user,
            password=self._settings.postgres_password,
            connect_timeout=self._settings.postgres_connect_timeout_seconds,
            row_factory=dict_row,
        )


    @staticmethod
    def _require_row(row: dict[str, Any] | None, message: str) -> dict[str, Any]:
        if row is None:
            raise SnapshotRepositoryError(message)
        return row


    def upsert_repository(
        self,
        *,
        tenant_id: str,
        repository_id: str,
        provider_repository_id: str,
        repository_full_name: str,
        repository_url: str,
        default_branch: str,
    ) -> dict[str, Any]:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    INSERT INTO snapshot_mvp_repositories (
                        tenant_id,
                        repository_id,
                        provider,
                        provider_repository_id,
                        repository_full_name,
                        repository_url,
                        default_branch,
                        visibility
                    ) VALUES (
                        %(tenant_id)s,
                        %(repository_id)s,
                        'github',
                        %(provider_repository_id)s,
                        %(repository_full_name)s,
                        %(repository_url)s,
                        %(default_branch)s,
                        'public'
                    )
                    ON CONFLICT (tenant_id, provider, provider_repository_id)
                    DO UPDATE SET
                        repository_full_name = EXCLUDED.repository_full_name,
                        repository_url = EXCLUDED.repository_url,
                        default_branch = EXCLUDED.default_branch,
                        visibility = 'public',
                        updated_at = NOW()
                    RETURNING *
                    """,
                    {
                        "tenant_id": tenant_id,
                        "repository_id": repository_id,
                        "provider_repository_id": provider_repository_id,
                        "repository_full_name": repository_full_name,
                        "repository_url": repository_url,
                        "default_branch": default_branch,
                    },
                ).fetchone()
        except psycopg.Error as exc:
            raise SnapshotRepositoryError(
                f"Failed to register GitHub repository: {exc}"
            ) from exc
        return self._require_row(row, "GitHub repository registration returned no row")


    def get_repository(self, tenant_id: str, repository_id: str) -> dict[str, Any] | None:
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT *
                    FROM snapshot_mvp_repositories
                    WHERE tenant_id = %(tenant_id)s
                      AND repository_id = %(repository_id)s
                      AND provider = 'github'
                      AND visibility = 'public'
                    """,
                    {"tenant_id": tenant_id, "repository_id": repository_id},
                ).fetchone()
        except psycopg.Error as exc:
            raise SnapshotRepositoryError(
                f"Failed to read GitHub repository: {exc}"
            ) from exc


    def list_repositories(
        self,
        tenant_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(limit, 500))
        bounded_offset = max(0, offset)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM snapshot_mvp_repositories
                    WHERE tenant_id = %(tenant_id)s
                      AND provider = 'github'
                      AND visibility = 'public'
                    ORDER BY created_at DESC
                    LIMIT %(limit)s
                    OFFSET %(offset)s
                    """,
                    {
                        "tenant_id": tenant_id,
                        "limit": bounded_limit,
                        "offset": bounded_offset,
                    },
                ).fetchall()
        except psycopg.Error as exc:
            raise SnapshotRepositoryError(
                f"Failed to list GitHub repositories: {exc}"
            ) from exc
        return list(rows)


    def admin_status(self, tenant_id: str) -> dict[str, int]:
        try:
            with self._connect() as connection:
                table_row = connection.execute(
                    """
                    SELECT
                        (CASE WHEN to_regclass('public.snapshot_mvp_repositories') IS NOT NULL THEN 1 ELSE 0 END
                       + CASE WHEN to_regclass('public.snapshot_mvp_snapshots') IS NOT NULL THEN 1 ELSE 0 END
                       + CASE WHEN to_regclass('public.snapshot_mvp_locators') IS NOT NULL THEN 1 ELSE 0 END)
                        AS table_count
                    """
                ).fetchone()
                table_count = int((table_row or {}).get("table_count") or 0)
                if table_count != 3:
                    return {
                        "table_count": table_count,
                        "repositories": 0,
                        "snapshots": 0,
                        "locators": 0,
                    }
                count_row = connection.execute(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM snapshot_mvp_repositories
                         WHERE tenant_id = %(tenant_id)s
                           AND provider = 'github'
                           AND visibility = 'public') AS repositories,
                        (SELECT COUNT(*) FROM snapshot_mvp_snapshots
                         WHERE tenant_id = %(tenant_id)s
                           AND snapshot_type = 'commit') AS snapshots,
                        (SELECT COUNT(*) FROM snapshot_mvp_locators
                         WHERE tenant_id = %(tenant_id)s
                           AND provider = 'github'
                           AND access_mode = 'backend-proxy') AS locators
                    """,
                    {"tenant_id": tenant_id},
                ).fetchone()
        except psycopg.Error as exc:
            raise SnapshotRepositoryError(
                f"Failed to inspect Snapshot storage: {exc}"
            ) from exc
        return {
            "table_count": table_count,
            "repositories": int((count_row or {}).get("repositories") or 0),
            "snapshots": int((count_row or {}).get("snapshots") or 0),
            "locators": int((count_row or {}).get("locators") or 0),
        }


    def register_verified_snapshot(
        self,
        *,
        tenant_id: str,
        snapshot_id: str,
        repository_id: str,
        commit_sha: str,
        tree_sha: str,
        fingerprint: str,
        locator_id: str,
        locator_details: dict[str, Any],
        verified_at: datetime,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        """Create Snapshot and GitHub locator in one database transaction."""


        try:
            with self._connect() as connection:
                existing = connection.execute(
                    """
                    SELECT *
                    FROM snapshot_mvp_snapshots
                    WHERE tenant_id = %(tenant_id)s
                      AND repository_id = %(repository_id)s
                      AND commit_sha = %(commit_sha)s
                    FOR SHARE
                    """,
                    {
                        "tenant_id": tenant_id,
                        "repository_id": repository_id,
                        "commit_sha": commit_sha,
                    },
                ).fetchone()
                deduplicated = existing is not None


                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO snapshot_mvp_snapshots (
                            tenant_id,
                            snapshot_id,
                            repository_id,
                            snapshot_type,
                            commit_sha,
                            tree_sha,
                            fingerprint,
                            verified_by,
                            verified_at
                        ) VALUES (
                            %(tenant_id)s,
                            %(snapshot_id)s,
                            %(repository_id)s,
                            'commit',
                            %(commit_sha)s,
                            %(tree_sha)s,
                            %(fingerprint)s,
                            'github',
                            %(verified_at)s
                        )
                        ON CONFLICT (tenant_id, repository_id, commit_sha)
                        DO NOTHING
                        """,
                        {
                            "tenant_id": tenant_id,
                            "snapshot_id": snapshot_id,
                            "repository_id": repository_id,
                            "commit_sha": commit_sha,
                            "tree_sha": tree_sha,
                            "fingerprint": fingerprint,
                            "verified_at": verified_at,
                        },
                    )
                    snapshot_row = connection.execute(
                        """
                        SELECT *
                        FROM snapshot_mvp_snapshots
                        WHERE tenant_id = %(tenant_id)s
                          AND repository_id = %(repository_id)s
                          AND commit_sha = %(commit_sha)s
                        FOR SHARE
                        """,
                        {
                            "tenant_id": tenant_id,
                            "repository_id": repository_id,
                            "commit_sha": commit_sha,
                        },
                    ).fetchone()
                    snapshot_row = self._require_row(
                        snapshot_row,
                        "Snapshot insert did not produce a durable row",
                    )
                    # The deterministic ID is identical on a concurrent replay.
                    # Reaching this SELECT after DO NOTHING means another
                    # transaction already created the same immutable Snapshot.
                    deduplicated = True
                else:
                    snapshot_row = existing


                if snapshot_row["tree_sha"] != tree_sha:
                    raise SnapshotIntegrityError(
                        "The stored commit SHA is already bound to a different tree SHA"
                    )
                if snapshot_row["fingerprint"] != fingerprint:
                    raise SnapshotIntegrityError(
                        "The stored commit SHA is already bound to a different fingerprint"
                    )


                effective_snapshot_id = str(snapshot_row["snapshot_id"])
                effective_locator_id = locator_id
                if effective_snapshot_id != snapshot_id:
                    effective_locator_id = f"loc_{fingerprint[:24]}"


                locator_row = connection.execute(
                    """
                    INSERT INTO snapshot_mvp_locators (
                        tenant_id,
                        locator_id,
                        snapshot_id,
                        provider,
                        access_mode,
                        availability,
                        details,
                        last_verified_at
                    ) VALUES (
                        %(tenant_id)s,
                        %(locator_id)s,
                        %(snapshot_id)s,
                        'github',
                        'backend-proxy',
                        'durable',
                        %(details)s,
                        %(verified_at)s
                    )
                    ON CONFLICT (tenant_id, snapshot_id, provider, access_mode)
                    DO UPDATE SET
                        availability = 'durable',
                        details = EXCLUDED.details,
                        last_verified_at = EXCLUDED.last_verified_at,
                        updated_at = NOW()
                    RETURNING *
                    """,
                    {
                        "tenant_id": tenant_id,
                        "locator_id": effective_locator_id,
                        "snapshot_id": effective_snapshot_id,
                        "details": Jsonb(locator_details),
                        "verified_at": verified_at,
                    },
                ).fetchone()
        except SnapshotRepositoryError:
            raise
        except psycopg.Error as exc:
            raise SnapshotRepositoryError(
                f"Failed to register verified GitHub Snapshot: {exc}"
            ) from exc


        return (
            self._require_row(snapshot_row, "Snapshot registration returned no row"),
            self._require_row(locator_row, "Locator registration returned no row"),
            deduplicated,
        )


    def get_snapshot(self, tenant_id: str, snapshot_id: str) -> dict[str, Any] | None:
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT *
                    FROM snapshot_mvp_snapshots
                    WHERE tenant_id = %(tenant_id)s
                      AND snapshot_id = %(snapshot_id)s
                      AND snapshot_type = 'commit'
                    """,
                    {"tenant_id": tenant_id, "snapshot_id": snapshot_id},
                ).fetchone()
        except psycopg.Error as exc:
            raise SnapshotRepositoryError(f"Failed to read Snapshot: {exc}") from exc


    def get_github_locator(
        self,
        tenant_id: str,
        snapshot_id: str,
    ) -> dict[str, Any] | None:
        try:
            with self._connect() as connection:
                return connection.execute(
                    """
                    SELECT *
                    FROM snapshot_mvp_locators
                    WHERE tenant_id = %(tenant_id)s
                      AND snapshot_id = %(snapshot_id)s
                      AND provider = 'github'
                      AND access_mode = 'backend-proxy'
                    """,
                    {"tenant_id": tenant_id, "snapshot_id": snapshot_id},
                ).fetchone()
        except psycopg.Error as exc:
            raise SnapshotRepositoryError(
                f"Failed to read GitHub Snapshot locator: {exc}"
            ) from exc


    def list_snapshots(
        self,
        tenant_id: str,
        repository_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(limit, 500))
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM snapshot_mvp_snapshots
                    WHERE tenant_id = %(tenant_id)s
                      AND repository_id = %(repository_id)s
                      AND snapshot_type = 'commit'
                    ORDER BY created_at DESC
                    LIMIT %(limit)s
                    """,
                    {
                        "tenant_id": tenant_id,
                        "repository_id": repository_id,
                        "limit": bounded_limit,
                    },
                ).fetchall()
        except psycopg.Error as exc:
            raise SnapshotRepositoryError(f"Failed to list Snapshots: {exc}") from exc
        return list(rows)


    def list_snapshots_for_tenant(
        self,
        tenant_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(limit, 500))
        bounded_offset = max(0, offset)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM snapshot_mvp_snapshots
                    WHERE tenant_id = %(tenant_id)s
                      AND snapshot_type = 'commit'
                    ORDER BY created_at DESC
                    LIMIT %(limit)s
                    OFFSET %(offset)s
                    """,
                    {
                        "tenant_id": tenant_id,
                        "limit": bounded_limit,
                        "offset": bounded_offset,
                    },
                ).fetchall()
        except psycopg.Error as exc:
            raise SnapshotRepositoryError(
                f"Failed to list snapshots for tenant: {exc}"
            ) from exc
        return list(rows)
