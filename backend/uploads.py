from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
from contextlib import contextmanager
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable, ContextManager, Iterator
from uuid import uuid4

from .config import Settings
from .schemas import (
    DocumentInput,
    UploadCreateRequest,
    UploadManifestPageRequest,
    UploadProgressResponse,
)


class UploadError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class UploadManager:
    _CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")

    def __init__(
        self,
        settings: Settings,
        *,
        lock_factory: Callable[[str], ContextManager[None]] | None = None,
    ) -> None:
        self.settings = settings
        self.root = settings.upload_root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock_factory = lock_factory
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def _local_lock(self, upload_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(upload_id, threading.RLock())

    @contextmanager
    def _guard(self, upload_id: str) -> Iterator[None]:
        """Serialize upload state across threads and API/worker replicas.

        The filesystem contains bulk bytes, while Redis owns cross-replica
        coordination.  This lets the same upload workspace live on an RWX PVC
        without assuming that one process or one Kubernetes node owns it.
        """
        with self._local_lock(upload_id):
            if self._lock_factory is None:
                yield
                return
            manager = self._lock_factory(f"upload:{upload_id}")
            try:
                manager.__enter__()
            except Exception as exc:
                raise UploadError(
                    "공유 업로드 잠금을 획득할 수 없습니다.", 503
                ) from exc
            try:
                yield
            finally:
                manager.__exit__(None, None, None)

    def storage_status(self) -> dict[str, Any]:
        return {
            "backend": "shared-filesystem",
            "root": str(self.root),
            "exists": self.root.exists(),
            "writable": self.root.exists() and self.root.is_dir() and os.access(self.root, os.W_OK),
            "distributed_lock": self._lock_factory is not None,
        }

    def _session_dir(self, upload_id: str) -> Path:
        if not re.fullmatch(r"upl_[a-f0-9]{32}", upload_id):
            raise UploadError("올바르지 않은 upload_id입니다.", 404)
        return self.root / upload_id

    def _state_path(self, upload_id: str) -> Path:
        return self._session_dir(upload_id) / "session.json"

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _read_state(self, upload_id: str) -> dict[str, Any]:
        path = self._state_path(upload_id)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise UploadError("업로드 세션을 찾을 수 없습니다.", 404) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise UploadError("업로드 세션 상태를 읽을 수 없습니다.", 500) from exc

    def _write_state(self, upload_id: str, state: dict[str, Any]) -> None:
        path = self._state_path(upload_id)
        temporary = path.with_suffix(".tmp")
        state["updated_at"] = self._now().isoformat()
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(path)

    def create(self, payload: UploadCreateRequest) -> UploadProgressResponse:
        upload_id = f"upl_{uuid4().hex}"
        directory = self._session_dir(upload_id)
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "parts").mkdir()
        (directory / "manifest").mkdir()
        (directory / "repository").mkdir()
        now = self._now()
        expires_at = now + timedelta(hours=self.settings.upload_session_ttl_hours)
        state: dict[str, Any] = {
            "upload_id": upload_id,
            "schema_version": payload.schema_version,
            "project_id": payload.project_id,
            "snapshot_id": payload.snapshot_id,
            "document_count": payload.document_count,
            "total_bytes": payload.total_bytes,
            "manifest_sha256": (
                payload.manifest_sha256.lower()
                if payload.manifest_sha256
                else None
            ),
            "modified_at": (
                payload.modified_at.isoformat() if payload.modified_at else None
            ),
            "git": payload.git.model_dump(mode="json") if payload.git else None,
            "status": "created",
            "part_size": self.settings.upload_part_size_bytes,
            "max_concurrency": 4,
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "manifest_entries": 0,
            "manifest_complete": False,
            "files_received": 0,
            "bytes_received": 0,
            "documents_processed": 0,
            "chunks_stored": 0,
            "failed_documents": 0,
            "error": None,
        }
        self._write_state(upload_id, state)
        return self._to_response(state)

    @staticmethod
    def _to_response(state: dict[str, Any]) -> UploadProgressResponse:
        return UploadProgressResponse(
            upload_id=state["upload_id"],
            project_id=state["project_id"],
            snapshot_id=state["snapshot_id"],
            status=state["status"],
            part_size=state["part_size"],
            max_concurrency=state.get("max_concurrency", 4),
            expires_at=datetime.fromisoformat(state["expires_at"]),
            manifest_entries=state.get("manifest_entries", 0),
            files_received=state.get("files_received", 0),
            bytes_received=state.get("bytes_received", 0),
            documents_processed=state.get("documents_processed", 0),
            chunks_stored=state.get("chunks_stored", 0),
            failed_documents=state.get("failed_documents", 0),
            error=state.get("error"),
        )

    def get(self, upload_id: str) -> UploadProgressResponse:
        return self._to_response(self._read_state(upload_id))

    def list_index_jobs(
        self,
        *,
        project_id: str | None = None,
        active_only: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        terminal_statuses = {"completed", "failed", "cancelled"}
        states: list[dict[str, Any]] = []
        for session_file in self.root.glob("upl_*/session.json"):
            try:
                state = json.loads(session_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not state.get("job_id"):
                continue
            if project_id and state.get("project_id") != project_id:
                continue
            if active_only and state.get("status") in terminal_statuses:
                continue
            if not state.get("updated_at"):
                state["updated_at"] = datetime.fromtimestamp(
                    session_file.stat().st_mtime,
                    tz=timezone.utc,
                ).isoformat()
            states.append(state)
        states.sort(
            key=lambda state: str(
                state.get("updated_at") or state.get("created_at") or ""
            ),
            reverse=True,
        )
        return states[:limit]

    def version_info(self, upload_id: str) -> dict[str, Any]:
        state = self._read_state(upload_id)
        return {
            "manifest_sha256": state.get("manifest_sha256"),
            "modified_at": state.get("modified_at"),
            "git": state.get("git"),
            "document_count": int(state.get("document_count") or 0),
            "total_bytes": int(state.get("total_bytes") or 0),
        }

    def add_manifest(
        self, upload_id: str, payload: UploadManifestPageRequest
    ) -> UploadProgressResponse:
        with self._guard(upload_id):
            state = self._read_state(upload_id)
            if state["status"] not in {"created", "uploading"}:
                raise UploadError("현재 상태에서는 manifest를 변경할 수 없습니다.", 409)
            added = 0
            for entry in payload.entries:
                serialized = entry.model_dump(mode="json")
                manifest_path = self._manifest_path(
                    self._session_dir(upload_id), entry.file_id
                )
                if manifest_path.exists():
                    existing = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if existing != serialized:
                        raise UploadError(
                            f"동일 file_id에 다른 manifest가 등록됐습니다: {entry.file_id}",
                            409,
                        )
                    continue
                temporary = manifest_path.with_suffix(".tmp")
                temporary.write_text(
                    json.dumps(serialized, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                temporary.replace(manifest_path)
                added += 1
            state["manifest_entries"] += added
            state["manifest_complete"] = not payload.has_more
            state["status"] = "uploading"
            self._write_state(upload_id, state)
            return self._to_response(state)

    @staticmethod
    def _part_directory(session_dir: Path, file_id: str) -> Path:
        safe_id = hashlib.sha256(file_id.encode("utf-8")).hexdigest()
        return session_dir / "parts" / safe_id

    @staticmethod
    def _manifest_path(session_dir: Path, file_id: str) -> Path:
        safe_id = hashlib.sha256(file_id.encode("utf-8")).hexdigest()
        return session_dir / "manifest" / f"{safe_id}.json"

    @staticmethod
    def _normalize_digest(value: str | None) -> str | None:
        if not value:
            return None
        digest = value.strip()
        if digest.lower().startswith("sha-256="):
            raw = digest.split("=", 1)[1]
            try:
                return base64.b64decode(raw, validate=True).hex()
            except (ValueError, binascii.Error) as exc:
                raise UploadError("Digest header가 올바르지 않습니다.", 400) from exc
        if re.fullmatch(r"[a-fA-F0-9]{64}", digest):
            return digest.lower()
        raise UploadError("Digest는 SHA-256 hex 또는 sha-256=base64 형식이어야 합니다.")

    async def write_part(
        self,
        upload_id: str,
        file_id: str,
        part_number: int,
        content_range: str | None,
        digest_header: str | None,
        stream: AsyncIterator[bytes],
    ) -> dict[str, Any]:
        if part_number < 1:
            raise UploadError("part_number는 1 이상이어야 합니다.", 422)
        match = self._CONTENT_RANGE.fullmatch(content_range or "")
        if not match:
            raise UploadError("Content-Range header가 필요합니다.", 400)
        start, end, total = (int(value) for value in match.groups())
        expected_bytes = end - start + 1
        if expected_bytes < 1 or expected_bytes > self.settings.upload_part_size_bytes:
            raise UploadError("업로드 part 크기가 허용 범위를 초과했습니다.", 413)
        expected_start = (part_number - 1) * self.settings.upload_part_size_bytes
        if start != expected_start:
            raise UploadError("part_number와 Content-Range 시작 위치가 일치하지 않습니다.", 409)
        expected_digest = self._normalize_digest(digest_header)
        if expected_digest is None:
            raise UploadError(
                "Digest 또는 X-Content-SHA256 header가 필요합니다.", 400
            )
        with self._guard(upload_id):
            state = self._read_state(upload_id)
            manifest_path = self._manifest_path(self._session_dir(upload_id), file_id)
            try:
                entry = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError) as exc:
                raise UploadError("manifest에 등록된 파일이 아닙니다.", 404) from exc
            if entry["entry_type"] != "file":
                raise UploadError("manifest에 등록된 파일이 아닙니다.", 404)
            if total != int(entry["size_bytes"]):
                raise UploadError("Content-Range 전체 크기가 manifest와 다릅니다.", 409)
        part_dir = self._part_directory(self._session_dir(upload_id), file_id)
        part_dir.mkdir(parents=True, exist_ok=True)
        part_path = part_dir / f"{part_number:08d}.part"
        record_path = part_dir / f"{part_number:08d}.json"
        temporary = part_path.with_suffix(".tmp")
        hasher = hashlib.sha256()
        written = 0
        with temporary.open("wb") as output:
            async for block in stream:
                if not block:
                    continue
                written += len(block)
                if written > expected_bytes or written > self.settings.upload_part_size_bytes:
                    output.close()
                    temporary.unlink(missing_ok=True)
                    raise UploadError("전송된 part 크기가 Content-Range를 초과했습니다.", 413)
                hasher.update(block)
                output.write(block)
        actual_digest = hasher.hexdigest()
        if written != expected_bytes:
            temporary.unlink(missing_ok=True)
            raise UploadError("전송된 part 크기가 Content-Range와 일치하지 않습니다.", 409)
        if expected_digest and actual_digest != expected_digest:
            temporary.unlink(missing_ok=True)
            raise UploadError("업로드 part checksum이 일치하지 않습니다.", 409)
        temporary.replace(part_path)
        with self._guard(upload_id):
            state = self._read_state(upload_id)
            previous = None
            if record_path.exists():
                previous = json.loads(record_path.read_text(encoding="utf-8"))
            record = {
                "part_number": part_number,
                "start": start,
                "end": end,
                "bytes": written,
                "sha256": actual_digest,
            }
            record_temporary = record_path.with_suffix(".tmp")
            record_temporary.write_text(
                json.dumps(record, separators=(",", ":")), encoding="utf-8"
            )
            record_temporary.replace(record_path)
            if previous is None:
                state["bytes_received"] += written
            else:
                state["bytes_received"] += written - int(previous["bytes"])
            state["status"] = "uploading"
            self._write_state(upload_id, state)
        return {
            "file_id": file_id,
            "part_number": part_number,
            "bytes_received": written,
            "checksum": actual_digest,
            "checksum_verified": expected_digest is None or expected_digest == actual_digest,
        }

    def queue(self, upload_id: str) -> tuple[str, UploadProgressResponse]:
        with self._guard(upload_id):
            state = self._read_state(upload_id)
            if not state.get("manifest_complete"):
                raise UploadError("manifest 전송이 완료되지 않았습니다.", 409)
            if state["status"] in {"queued", "indexing", "completed"}:
                return state.get("job_id", f"job_{upload_id[4:]}"), self._to_response(state)
            manifest_paths = list(
                (self._session_dir(upload_id) / "manifest").glob("*.json")
            )
            file_count = 0
            total_bytes = 0
            relative_paths: set[str] = set()
            manifest_entries: list[dict[str, Any]] = []
            for manifest_path in manifest_paths:
                try:
                    entry = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise UploadError("manifest 파일을 읽을 수 없습니다.", 500) from exc
                manifest_entries.append(entry)
                relative_path = str(entry["relative_path"])
                if relative_path in relative_paths:
                    raise UploadError(
                        f"중복 relative_path가 있습니다: {relative_path}", 409
                    )
                relative_paths.add(relative_path)
                if entry["entry_type"] == "file":
                    file_count += 1
                    total_bytes += int(entry["size_bytes"])
            if file_count != int(state["document_count"]):
                raise UploadError(
                    "manifest 파일 수가 document_count와 일치하지 않습니다.", 409
                )
            if total_bytes != int(state["total_bytes"]):
                raise UploadError(
                    "manifest 전체 파일 크기가 total_bytes와 일치하지 않습니다.", 409
                )
            canonical_manifest = json.dumps(
                sorted(
                    manifest_entries,
                    key=lambda item: (str(item["relative_path"]), str(item["file_id"])),
                ),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            actual_manifest_sha256 = hashlib.sha256(canonical_manifest).hexdigest()
            supplied_manifest_sha256 = state.get("manifest_sha256")
            if (
                supplied_manifest_sha256
                and supplied_manifest_sha256 != actual_manifest_sha256
            ):
                raise UploadError(
                    "manifest_sha256이 전송된 manifest 내용과 일치하지 않습니다.",
                    409,
                )
            state["manifest_sha256"] = actual_manifest_sha256
            state["status"] = "queued"
            state["job_id"] = f"job_{uuid4().hex}"
            self._write_state(upload_id, state)
            return state["job_id"], self._to_response(state)

    def mark_queue_failed(self, upload_id: str, error: str) -> None:
        """Make a failed dispatch retryable by a later complete request."""

        with self._guard(upload_id):
            state = self._read_state(upload_id)
            if state["status"] == "queued":
                state["status"] = "failed"
                state["error"] = error[:1000]
                state["completed_at"] = self._now().isoformat()
                self._write_state(upload_id, state)

    @staticmethod
    def _decode_text(path: Path, maximum_bytes: int) -> str | None:
        if path.stat().st_size > maximum_bytes:
            return None
        raw = path.read_bytes()
        if b"\x00" in raw[:8192] and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            return None
        for encoding in ("utf-8-sig", "utf-16", "cp949"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return None

    def process(
        self,
        upload_id: str,
        ingest_document: Callable[[str, DocumentInput], int],
    ) -> None:
        try:
            with self._guard(upload_id):
                state = self._read_state(upload_id)
                state["status"] = "indexing"
                self._write_state(upload_id, state)
            session_dir = self._session_dir(upload_id)
            received_files = 0
            for manifest_path in (session_dir / "manifest").glob("*.json"):
                entry = json.loads(manifest_path.read_text(encoding="utf-8"))
                file_id = entry["file_id"]
                if entry["entry_type"] != "file":
                    continue
                part_dir = self._part_directory(session_dir, file_id)
                records = []
                if part_dir.exists():
                    for record_path in part_dir.glob("*.json"):
                        records.append(
                            json.loads(record_path.read_text(encoding="utf-8"))
                        )
                records.sort(key=lambda item: item["start"])
                if not records and int(entry["size_bytes"]) > 0:
                    raise UploadError(f"파일 part가 없습니다: {entry['relative_path']}", 409)
                expected_start = 0
                destination = session_dir / "repository" / entry["relative_path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                file_hasher = hashlib.sha256()
                with destination.open("wb") as output:
                    for record in records:
                        if int(record["start"]) != expected_start:
                            raise UploadError(
                                f"파일 part가 연속적이지 않습니다: {entry['relative_path']}",
                                409,
                            )
                        part_path = self._part_directory(session_dir, file_id) / (
                            f"{int(record['part_number']):08d}.part"
                        )
                        with part_path.open("rb") as source:
                            while block := source.read(1024 * 1024):
                                file_hasher.update(block)
                                output.write(block)
                        expected_start = int(record["end"]) + 1
                if destination.stat().st_size != int(entry["size_bytes"]):
                    raise UploadError(f"파일 크기 검증 실패: {entry['relative_path']}", 409)
                if entry.get("sha256") and file_hasher.hexdigest() != entry["sha256"].lower():
                    raise UploadError(f"파일 checksum 검증 실패: {entry['relative_path']}", 409)
                received_files += 1
                text = self._decode_text(destination, self.settings.max_indexable_file_bytes)
                chunks_stored = 0
                failed = 0
                if text is not None and text.strip():
                    try:
                        chunks_stored = ingest_document(
                            state["project_id"],
                            DocumentInput(
                                document_id=file_id,
                                text=text,
                                path=entry["relative_path"],
                                language=entry.get("language_hint"),
                                metadata={
                                    "snapshot_id": state["snapshot_id"],
                                    "sha256": file_hasher.hexdigest(),
                                    "size_bytes": entry["size_bytes"],
                                    "content_type_hint": entry.get("content_type_hint"),
                                },
                            ),
                        )
                    except Exception:
                        failed = 1
                with self._guard(upload_id):
                    current = self._read_state(upload_id)
                    current["files_received"] = received_files
                    current["documents_processed"] += 1
                    current["chunks_stored"] += chunks_stored
                    current["failed_documents"] += failed
                    self._write_state(upload_id, current)
            with self._guard(upload_id):
                current = self._read_state(upload_id)
                current["status"] = (
                    "completed" if current["failed_documents"] == 0 else "failed"
                )
                current["completed_at"] = self._now().isoformat()
                if current["failed_documents"]:
                    current["error"] = "일부 문서 인덱싱에 실패했습니다."
                self._write_state(upload_id, current)
        except Exception as exc:
            with self._guard(upload_id):
                try:
                    state = self._read_state(upload_id)
                    state["status"] = "failed"
                    state["error"] = str(exc)[:1000]
                    state["completed_at"] = self._now().isoformat()
                    self._write_state(upload_id, state)
                except UploadError:
                    pass

    def cancel(self, upload_id: str) -> None:
        with self._guard(upload_id):
            directory = self._session_dir(upload_id)
            if not directory.exists():
                raise UploadError("업로드 세션을 찾을 수 없습니다.", 404)
            shutil.rmtree(directory)
        with self._locks_guard:
            self._locks.pop(upload_id, None)
