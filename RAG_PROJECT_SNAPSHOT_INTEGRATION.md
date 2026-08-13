# Vision Project / RAG / Snapshot Integration Contract

Baseline: `h5vision/api_test:main` at `c63d5243f636deb382b0136327da27d720405c06`.

This document freezes the Backend-side contract for the three pre-AI-team phases:
External Project Registry, Snapshot Hydration, and Workspace Git State comparison.

## Phase A — External Project Registry

PostgreSQL remains the authority for Vision project identity and Snapshot identity.
RAG Lab `/projects` remains the authority only for the externally observed index state.

Tables:

- `rag_targets`: stable RAG deployment identity. `target_id` survives URL/IP changes.
- `external_project_catalog`: observed `/projects` state, revision, counts, availability and raw metadata.
- `project_external_bindings`: logical Vision Project ↔ External Project identity only.

Bindings deliberately do **not** store `active_snapshot_id` or a mutable current revision.
Revision compatibility remains a Chat-time safety check.

Manual synchronization:

```powershell
python scripts/sync_external_projects.py
```

Environment:

```text
RAG_LAB_TARGET_ID=rag-lab-main
RAG_LAB_BASE_URL=http://<rag-host>:8200
```

Reconciliation order:

1. Existing manual binding is preserved.
2. Unique exact Snapshot/Git revision match → `verified`.
3. Exact project ID match → `verified`.
4. Unique leaf-name match → `candidate` only.
5. Ambiguous or missing match → no automatic verified binding.
6. Missing external rows become `stale`; they are not deleted automatically.

The existing live RAG revision verification used by project-grounded Chat remains in place.
Catalog synchronization is not a substitute for that runtime check.

## Phase B — Snapshot Hydration

Hydration is a read-only facade over existing `project_snapshots` and `snapshot_entries`.
It does not create a new Snapshot registry.

### Authentication

Send a service-only credential:

```text
X-Vision-Hydration-Token: <secret>
```

Configure at least 32 UTF-8 bytes through either:

```text
SNAPSHOT_HYDRATION_TOKEN=...
SNAPSHOT_HYDRATION_TOKEN_FILE=/run/secrets/...
```

The configured `SNAPSHOT_TENANT_ID` scopes all hydration reads. Knowing another tenant's
`snapshot_id` is insufficient to read it.

### Descriptor

```http
GET /v1/snapshot-hydrations/{snapshot_id}
```

Example:

```json
{
  "schema_version": "1.0",
  "snapshot_id": "snap_123",
  "project_id": "h5vision/fest-api",
  "source_type": "git",
  "snapshot_kind": "git-commit",
  "revision": "40-char-sha",
  "branch": "main",
  "dirty": false,
  "immutable": true,
  "manifest_sha256": "hydration-canonical-sha256",
  "source_manifest_sha256": "snapshot-source-manifest-sha256",
  "file_count": 123,
  "total_bytes": 456789,
  "capabilities": ["manifest.read", "file.read"]
}
```

`manifest_sha256` is the Hydration contract hash. It is calculated from all entries sorted
by project-relative POSIX `path`, using canonical JSON (`sort_keys=true`, compact separators)
and only these identity fields:

- `path`
- `entry_type`
- `size_bytes`
- `content_sha256`

`source_manifest_sha256` preserves the original Snapshot registry value for diagnostics.

### Manifest

```http
GET /v1/snapshot-hydrations/{snapshot_id}/manifest?limit=500&cursor=...
```

The cursor is opaque and HMAC-signed. Clients must return it unchanged.

Entry:

```json
{
  "path": "backend/app.py",
  "entry_type": "file",
  "language": "python",
  "size_bytes": 15240,
  "content_sha256": "raw-byte-sha256",
  "object_id": null,
  "indexable": true,
  "encoding": "utf-8"
}
```

Binary/non-materialized files stay in the manifest with `encoding=null` and/or
`indexable=false`.

### File

```http
GET /v1/snapshot-hydrations/{snapshot_id}/file?path=backend/app.py
```

MVP response is JSON text. Before returning the file, Backend re-encodes the stored text
with its recorded source encoding and verifies that the resulting SHA-256 is exactly the
stored **raw source byte** `content_sha256`. A mismatch returns a conflict instead of
pretending that the transport is byte-exact.

Response:

```json
{
  "schema_version": "1.0",
  "snapshot_id": "snap_123",
  "project_id": "h5vision/fest-api",
  "path": "backend/app.py",
  "size_bytes": 15240,
  "content_sha256": "raw-byte-sha256",
  "transport_sha256": "same-raw-byte-sha256",
  "encoding": "utf-8",
  "content": "..."
}
```

Future binary/large-file transport may use raw HTTP bytes with `ETag` and
`X-Content-SHA256` while preserving the same manifest contract.

Path rules:

- project-relative POSIX path only
- absolute paths rejected
- Windows drive paths rejected
- UNC paths rejected
- `.` and `..` traversal segments rejected

## Phase C — Workspace Git State / Snapshot Compare

Existing endpoint remains:

```http
POST /v1/snapshots/compare
```

`comparison` continues to mean Git/Snapshot revision identity only:

```text
same | different | unknown
```

Optional IDE state:

```json
{
  "project_id": "h5vision/fest-api",
  "commit_id": "40-char-head-sha",
  "snapshot_id": null,
  "git_state": {
    "branch": "main",
    "dirty": true,
    "working_tree_count": 2,
    "staged_count": 1,
    "merge_count": 0,
    "ahead": 0,
    "behind": 0
  }
}
```

Response adds an independent workspace axis:

```text
workspace_state = clean | modified | conflicted | unknown
workspace_matches_snapshot = true | false | null
```

Key semantics:

| Backend Snapshot | Frontend HEAD | Workspace | comparison | workspace_state | workspace_matches_snapshot |
|---|---|---|---|---|---|
| A | A | clean | same | clean | true |
| A | A | modified/staged | same | modified | false |
| A | A | merge conflict | same | conflicted | false |
| A | B | any | different | derived from git_state | false |
| none | any | any | unknown | derived from git_state | null |

A matching HEAD with dirty files therefore does **not** change `comparison` to `different`.
It returns `comparison=same` while setting `reason_code=working_tree_modified` and
`update_warning=true`.

## Frontend handoff

The VS Code extension should eventually source `commit_id` from its actual Git service,
not from a static `vision.commitId` setting, and call `/v1/snapshots/compare` on relevant
workspace/HEAD refresh points. File-save events should be locally debounced; a network
comparison is not required on every keystroke.


## Phase D — Revision Context / Commit Delta / AI Server Transport

The public Frontend Snapshot compare request remains unchanged. Phase D assigns stable
semantics to the existing fields and derives additional identities inside Backend:

- `commit_id` = Frontend-observed local HEAD SHA (`observed`).
- `git_state.branch` = Git ref Backend resolves against GitHub (`authoritative`).
- the selected project Snapshot revision = canonical source/Hydration base (`canonical`).

Backend normalizes three independent revision relations:

```text
local_vs_remote
local_vs_snapshot
remote_vs_snapshot
```

`POST /v1/snapshots/compare` keeps the frozen `same | different | unknown` contract and may
add backward-compatible optional `revision_context` and `revision_diff` diagnostics.
Authoritative GitHub ref resolution is read-only and never creates a Snapshot.

### Commit delta

The canonical project Snapshot revision is the diff base and the Frontend local HEAD is the
target. Diff status is explicit:

```text
not_needed | available | unavailable |
base_object_unavailable | target_object_unavailable | failed
```

An unpushed local commit that GitHub cannot resolve returns
`target_object_unavailable`; Backend never fabricates a patch from SHA values alone.
Dirty, staged and conflict state remains a separate workspace axis. The existing compare
payload can report dirty/count metadata, but it cannot reconstruct uncommitted file contents,
so dirty working-tree patch transport is outside Phase D.

GitHub compare output is bounded to 300 files and a 256 KiB aggregate patch-body budget.
File identity/change metadata remains available when patch text is truncated.

The diff also reports:

- `merge_base_sha`
- `patch_basis = none | snapshot | merge_base`
- `safe_to_apply_to_snapshot`

A patch is marked safe to apply directly to the canonical Snapshot only when the compare
merge base is the same revision as the Snapshot base. Diverged/behind history is marked
`patch_basis=merge_base` and `safe_to_apply_to_snapshot=false` so consumers do not treat a
merge-base-oriented delta as a direct Snapshot overlay.

### Canonical Snapshot bridge

GitHub Commit Snapshot IDs and canonical project Snapshot IDs are distinct registries. If an
explicit Snapshot ID belongs to the GitHub Snapshot control plane, Backend resolves its Commit
SHA and maps that revision to a completed `project_snapshots` record before Chat, Hydration or
AI Server transport. The ID sent downstream is therefore the canonical project Snapshot ID.

### Observation cache

Revision context and diff may be cached best-effort in Redis under the
`revision-observation` namespace for 900 seconds, keyed by Frontend owner/client and project.
The cache is an optimization only: Redis failure does not change compare or Chat correctness.
Chat reuses an observation only when the selected canonical Snapshot ID/revision and the
available local SHA still match; otherwise it re-resolves the revision context.

### AI Server transport

Small routing identities are sent as `X-Vision-*` headers, including Snapshot/base/target/
remote revision and diff status. Structured project/Snapshot/revision/workspace/diff data is
sent in the top-level AI request field `vision_context`.

Streaming and non-streaming BackendAI/Ollama paths carry the same revision context. Existing
RAG Lab project/revision verification still occurs before generation.

## Non-goals of these phases

- No Chroma → Qdrant migration.
- No replacement of current RAG Lab `/prompt` Chat path.
- No removal of Chat-time external revision verification.
- No AI model inference-logic change; Phase D only enriches Snapshot/revision transport.
- No automatic creation of a new Snapshot merely because the Frontend HEAD differs.
- No Frontend payload shape change.
- No `repository_refs` migration or full Git history catalog in Phase D.
- No upload of local-only Git objects or dirty working-tree patch bodies in Phase D.

## Backend deployment order

The schema guard moves to `p3_0010_external_project_registry`, so code and database migration
must be deployed as one release unit.

```text
1. Configure RAG_LAB_TARGET_ID and SNAPSHOT_HYDRATION_TOKEN(_FILE).
2. Run: alembic upgrade head
3. Restart Backend replicas.
4. Run: python scripts/sync_external_projects.py
5. Smoke-test Snapshot compare and Hydration endpoints with a known Snapshot.
6. Smoke-test `revision_context` / `revision_diff` with a known GitHub ref and Commit pair.
7. Smoke-test both streaming and non-streaming AI Server receipt of `X-Vision-*` headers and `vision_context`.
8. Keep the existing project-grounded Chat `/projects` revision verification enabled.
```

Do not deploy the new `schema_guard.py` against a database that is still on
`p3_0009_chat_intake_normalization`; all PostgreSQL-backed stores intentionally fail closed
until `alembic upgrade head` completes.
