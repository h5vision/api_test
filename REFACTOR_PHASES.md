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
| 1 | complete | `ecb32231725aeacddf74d5a5fdd9b43f2f2faf8c` | `1ac2030157031be736d282cb93ad771bbbcb4cef` | Move `/v1/models` route ownership to `api/v1/models.py` and establish the Models contract path without changing Pydantic identity. |
| 2 | complete | `1ac2030157031be736d282cb93ad771bbbcb4cef` | `3bee97f4e0965efef2e3e550f1c194e6d9bee8e3` | Move public health and VS Code language registry routes into `api/v1/system.py`. |
| 3 | complete | `3bee97f4e0965efef2e3e550f1c194e6d9bee8e3` | `ef9759bb50c8f65212cbfb01183e29d1822c40bb` | Move public project discovery, briefing, tree, file, metadata, and version-check route ownership into `api/v1/projects.py`. |
| 4 | complete | `ef9759bb50c8f65212cbfb01183e29d1822c40bb` | `a95a4f3e8fc8f32a184ba2eeb33bb627959f2762` | Move public Snapshot comparison route ownership into `api/v1/snapshots.py` while retaining existing Snapshot services and feature-flagged control plane. |
| 5 | complete | `a95a4f3e8fc8f32a184ba2eeb33bb627959f2762` | `9f8f9c575802ece0985f8734d7d72a79a99402e6` | Move GitHub Snapshot adapter implementations under `integrations/github/` and keep legacy import paths as compatibility shims. |
| 6 | complete | `9f8f9c575802ece0985f8734d7d72a79a99402e6` | `fe6d2c731cafe1adc8705b90943231136dd59404` | Move VectorIndex registries/runtime into `domains/vector_indexes/`, Qdrant I/O into `integrations/vectordb/`, and preserve legacy imports through shims. |
| 7 | complete | `fe6d2c731cafe1adc8705b90943231136dd59404` | `c0c48ffb2a51d52cbceeb47d627b71fd845fe60a` | Move repository source/indexing/upload implementations into `domains/repositories/`, extract 12 public routes into `api/v1/repositories.py`, and preserve legacy module paths through alias shims. |
| 8 | stabilizing | `c0c48ffb2a51d52cbceeb47d627b71fd845fe60a` | `9cd356251a97534cf2b0110e412673ac3328deb5` | Split generation into catalog/routing/context/contracts, move provider HTTP transport into `integrations/ai_server/`, and preserve the legacy `backend.generation` module through an alias. |


## Stabilization checkpoints

| Phase | Implementation commit | Stabilization commit | Validation |
| --- | --- | --- | --- |
| 8 | `9cd356251a97534cf2b0110e412673ac3328deb5` | pending | Python compile PASS; Generation/AI Server focused tests 15 passed; full suite 216 passed, 21 known/deferred structural failures, 2 skipped. Exact deferred nodeids are frozen in `PHASE8_STABILIZATION.md`. |
