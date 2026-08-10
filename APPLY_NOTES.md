# Vision Repository-centric Snapshot Admin change set

Apply these files over the matching paths in the Vision project.

## Included
- `backend/snapshots/router.py`: public repository list endpoint
- `backend/admin_snapshots.py`: admin repository-scoped snapshot list endpoint
- `admin/src/snapshots.ts`: clickable repository filter and all-snapshots reset
- related admin API/frontend contract tests

## Validation performed
- Python `py_compile`: passed
- TypeScript strict `tsc --noEmit`: passed
- Isolated pytest: 6 passed
- FastAPI public route registry check: passed

## Deliberately deferred
- Vector Index status in admin UI
- AI Server delivery status in admin UI
- requested_ref capture-history persistence

Those require durable DB/API contracts before surfacing them.
