# Backend Refactor Phase History

This branch tracks the structural refactor of the Vision backend while preserving the upstream `main` behavior and public API contracts.

## Baseline

- Upstream repository: `h5vision/api_test`
- Fork repository: `PoikileKerdo/api_test`
- Refactor branch: `Backend`
- Baseline commit: `858fc1b9881b9dbe67ae5a31c29176cba0c36bbf`
- Rule: upstream/fork `main` is not modified by the refactor.

## Phase order

0. Baseline and refactor tracking
1. Models
2. System / Languages / Health
3. Projects
4. Snapshots
5. GitHub integration
6. Vector indexes
7. Repository / indexing / upload
8. Generation
9. Chat supporting modules
10. RAG / retrieval
11. Chat router
12. Admin backend
13. Legacy `schemas.py` cleanup
14. `app.py` final composition cleanup
15. Admin frontend
16. Test structure alignment
17. Archive / cache / duplicate cleanup

## Migration strategy

The frozen monolithic application is retained temporarily as `backend/legacy_app.py`.
`backend/app.py` acts as a compatibility facade that replaces legacy routes one
domain at a time while preserving the historical `backend.app` module surface.
The legacy module is removed only after all route and service ownership has moved.

## Completion rule

Each phase is validated, committed to `Backend`, pushed, and recorded here with its resulting commit SHA before the next phase starts.

## Versions

| Phase | Status | Base | Commit | Summary |
| --- | --- | --- | --- | --- |
| 0 | complete | `858fc1b9881b9dbe67ae5a31c29176cba0c36bbf` | `ecb32231725aeacddf74d5a5fdd9b43f2f2faf8c` | Establish refactor baseline and phase tracking. |
| 1 | complete | `ecb32231725aeacddf74d5a5fdd9b43f2f2faf8c` | this commit | Move `/v1/models` route ownership to `api/v1/models.py` and establish the Models contract path without changing Pydantic identity. |
