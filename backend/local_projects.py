from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schemas import GitVersionInfo, ProjectTreeNode, ProjectVersionDescriptor


IGNORED_PROJECT_DIRECTORIES = frozenset(
    {".git", ".vscode", "node_modules", "dist", "build", "out", "coverage"}
)
MAX_PROJECT_TREE_ENTRIES = 10_000


class LocalProjectError(RuntimeError):
    pass


def _structure_digest(entries: list[dict[str, str]]) -> str:
    encoded = json.dumps(
        sorted(entries, key=lambda item: (item["path"], item["type"])),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def fingerprint_frontend_tree(
    tree: ProjectTreeNode,
) -> tuple[str, int, datetime | None]:
    entries: list[dict[str, str]] = []
    latest_modified_at: datetime | None = None

    def visit(node: ProjectTreeNode, relative_path: str) -> None:
        nonlocal latest_modified_at
        if len(entries) >= MAX_PROJECT_TREE_ENTRIES:
            raise LocalProjectError(
                f"project tree must not exceed {MAX_PROJECT_TREE_ENTRIES} entries"
            )
        entries.append({"path": relative_path, "type": node.type})
        if node.modified_time is not None and (
            latest_modified_at is None or node.modified_time > latest_modified_at
        ):
            latest_modified_at = node.modified_time
        if node.type != "directory":
            if node.children:
                raise LocalProjectError("file tree nodes must not contain children")
            return
        seen_names: set[str] = set()
        for child in sorted(node.children, key=lambda item: item.name):
            if child.name in seen_names:
                raise LocalProjectError(
                    f"duplicate tree entry under {relative_path}: {child.name}"
                )
            seen_names.add(child.name)
            if (
                child.type == "directory"
                and child.name in IGNORED_PROJECT_DIRECTORIES
            ):
                continue
            child_path = (
                child.name if relative_path == "." else f"{relative_path}/{child.name}"
            )
            visit(child, child_path)

    visit(tree, ".")
    return _structure_digest(entries), len(entries), latest_modified_at


class LocalProjectRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _resolve_project(self, project_id: str) -> Path | None:
        normalized = project_id.strip()
        if (
            not normalized
            or normalized in {".", ".."}
            or "/" in normalized
            or "\\" in normalized
        ):
            raise LocalProjectError("project_id must be one project folder name")
        try:
            root = self.root.resolve(strict=True)
        except OSError as exc:
            raise LocalProjectError("local project DB root is unavailable") from exc
        candidate = (
            root
            if normalized.casefold() == root.name.casefold()
            else root / normalized
        )
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError):
            return None
        return resolved if resolved.is_dir() else None

    @staticmethod
    def _git_value(target: Path, *arguments: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(target), *arguments],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        value = completed.stdout.strip()
        return value if completed.returncode == 0 and value else None

    def _git_info(self, target: Path) -> GitVersionInfo | None:
        commit_sha = self._git_value(target, "rev-parse", "HEAD")
        if not commit_sha:
            return None
        branch = self._git_value(target, "branch", "--show-current")
        committed_at_raw = self._git_value(target, "show", "-s", "--format=%cI", "HEAD")
        committed_at = None
        if committed_at_raw:
            try:
                committed_at = datetime.fromisoformat(committed_at_raw)
            except ValueError:
                committed_at = None
        status = self._git_value(
            target,
            "status",
            "--porcelain",
            "--untracked-files=normal",
            "--",
            ".",
        )
        return GitVersionInfo(
            commit_sha=commit_sha,
            branch=branch,
            dirty=bool(status),
            committed_at=committed_at,
        )

    @staticmethod
    def _scan_structure(target: Path) -> tuple[str, int, datetime | None]:
        entries: list[dict[str, str]] = [{"path": ".", "type": "directory"}]
        latest_timestamp = target.stat().st_mtime

        def visit(directory: Path, relative_directory: str) -> None:
            nonlocal latest_timestamp
            try:
                children = sorted(
                    os.scandir(directory),
                    key=lambda item: item.name,
                )
            except OSError as exc:
                raise LocalProjectError(
                    f"cannot read local project directory: {relative_directory}"
                ) from exc
            for child in children:
                try:
                    is_directory = child.is_dir(follow_symlinks=False)
                    if is_directory and child.name in IGNORED_PROJECT_DIRECTORIES:
                        continue
                    child_path = (
                        child.name
                        if relative_directory == "."
                        else f"{relative_directory}/{child.name}"
                    )
                    entries.append(
                        {
                            "path": child_path,
                            "type": "directory" if is_directory else "file",
                        }
                    )
                    if len(entries) > MAX_PROJECT_TREE_ENTRIES:
                        raise LocalProjectError(
                            "local project tree exceeds "
                            f"{MAX_PROJECT_TREE_ENTRIES} entries"
                        )
                    latest_timestamp = max(
                        latest_timestamp,
                        child.stat(follow_symlinks=False).st_mtime,
                    )
                    if is_directory:
                        visit(Path(child.path), child_path)
                except OSError as exc:
                    raise LocalProjectError(
                        f"cannot inspect local project entry: {child.path}"
                    ) from exc

        visit(target, ".")
        return (
            _structure_digest(entries),
            len(entries),
            datetime.fromtimestamp(latest_timestamp, timezone.utc),
        )

    def get_version(self, project_id: str) -> dict[str, Any] | None:
        target = self._resolve_project(project_id)
        if target is None:
            return None
        structure_sha256, entry_count, modified_at = self._scan_structure(target)
        git = self._git_info(target)
        return {
            "project_id": project_id,
            "current_snapshot_id": None,
            "manifest_sha256": None,
            "structure_sha256": structure_sha256,
            "entry_count": entry_count,
            "git_commit_sha": git.commit_sha if git else None,
            "git_branch": git.branch if git else None,
            "git_dirty": git.dirty if git else None,
            "git_committed_at": git.committed_at if git else None,
            "source_modified_at": modified_at,
            "updated_at": modified_at,
            "backend_source": "local",
        }
