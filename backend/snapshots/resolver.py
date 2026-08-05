from __future__ import annotations


from .contracts import AccessPlan, LocatorRecord, SnapshotRecord




class SnapshotResolutionError(RuntimeError):
    pass




class GithubSnapshotResolver:
    """Resolve one verified GitHub locator without score-based cross-client selection."""


    def resolve(
        self,
        snapshot: SnapshotRecord,
        locator: LocatorRecord | None,
    ) -> AccessPlan:
        if locator is None:
            return AccessPlan(
                snapshot_id=snapshot.snapshot_id,
                available=False,
                access_mode="unavailable",
                commit_sha=snapshot.commit_sha,
                tree_sha=snapshot.tree_sha,
                capabilities=[],
                reason="github_locator_missing",
            )
        if locator.snapshot_id != snapshot.snapshot_id:
            raise SnapshotResolutionError(
                "Locator and Snapshot identities do not match"
            )
        if locator.provider != "github" or locator.access_mode != "backend-proxy":
            raise SnapshotResolutionError(
                "The GitHub Snapshot MVP received an unsupported locator"
            )
        if locator.availability != "durable":
            return AccessPlan(
                snapshot_id=snapshot.snapshot_id,
                available=False,
                access_mode="unavailable",
                commit_sha=snapshot.commit_sha,
                tree_sha=snapshot.tree_sha,
                capabilities=[],
                reason="github_locator_unavailable",
            )


        return AccessPlan(
            snapshot_id=snapshot.snapshot_id,
            available=True,
            access_mode="backend-proxy",
            commit_sha=snapshot.commit_sha,
            tree_sha=snapshot.tree_sha,
            capabilities=["commit.read", "tree.read", "file.read"],
            tree_endpoint=(
                f"/v1/snapshot-control/snapshots/{snapshot.snapshot_id}/tree"
            ),
            file_endpoint=(
                f"/v1/snapshot-control/snapshots/{snapshot.snapshot_id}/file"
            ),
        )