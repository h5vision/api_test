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
| 8 | complete | `c0c48ffb2a51d52cbceeb47d627b71fd845fe60a` | `9cd356251a97534cf2b0110e412673ac3328deb5` | Split generation into catalog/routing/context/contracts, move provider HTTP transport into `integrations/ai_server/`, and preserve the legacy `backend.generation` module through an alias. |
| 9 | complete | `dd6ca8dc6991b0a6169b2c35b1105410492dddc4` | `07f325e8fc682e7f99049428b04f1718c1b137cb` | Move Chat intake/context/routing/streaming/canonical-context/progress ownership into `domains/chat/`, preserve legacy root imports through module aliases, and realign one source-path test to the canonical owner. |
| 10 | complete | `45eb2f4584ab33af8b1ce6535ffc6a0b9d54bdde` | `027f22c95be3c856e78d3b6cec79a5b0bbd90daf` | Move retrieval reranking/evaluation and agentic RAG into canonical domains, move the rag_lab VectorDB adapter under `integrations/vectordb/`, preserve legacy imports through module aliases, and isolate legacy test helpers that mutate `sys.modules`. |
| 11 | complete | `88e700742f977aa375bde38e3520a2b48e764764` | `ceb4063abb5e2d9b4b7167f0c361d239d983a4f2` | Move the public Chat route surface into `api/v1/chat.py` while preserving the existing legacy Chat handlers and orchestration behavior. |
| 12 | complete | `e18489ce3270b64da0133326564b1ca15b6b2b27` | `eb3725356796c7ccb99a89c4b38a4cf6acfb6bda` | Consolidate Admin route ownership in `api/v1/admin.py`, flatten nested included-router Admin routes while preserving existing `APIRoute` objects, and remove fully-owned legacy route containers with fail-closed partial-ownership handling. |
| 13 | complete | `4c9bbe4f077f1b9a5637373ade9e3ecae0c324f2` | `b4b35ffd62bcae7836584e979e7739a7b2032b38` | Move schema implementation ownership into canonical contract/domain modules, retain `backend.schemas` as an identity-preserving compatibility facade, and preserve the public OpenAPI contract exactly. |
| 14 | complete | `2390385e0d17141ec30a19bdc6b9ad5f929c8c31` | `6d7642289058f06d8645807cdf2bcde3f2871970` | Make `backend.app` the stable public composition module, remove whole-module replacement through `sys.modules`, and retain compatibility access to legacy handler owners without changing the ASGI app or route contract. |
| 15 | complete | `10d0a3bacb8c46ecff9fc980a80d38c7a385d64f` | `721214c6d02b364f7ee90ae9e4f88010dd98416e` | Establish Admin page, feature, shared API/type/component/utility ownership, reduce the monolithic entrypoint, and preserve all existing dashboard behavior and API contracts. |
| 16 | complete | `4247320262e2df3b927248736ebe3ad5a352dee6` | `dda08df4c145bd9eb76caa5231a277b8eff9e463` | Realign the frozen structural assertions with their canonical implementation owners without weakening their behavioral or contract checks. |


## Stabilization checkpoints

| Phase | Implementation commit | Stabilization commit | Validation |
| --- | --- | --- | --- |
| 8 | `9cd356251a97534cf2b0110e412673ac3328deb5` | `53a3060db103b6979da07df46307066bc8995930` | Python compile PASS; Generation/AI Server focused tests 15 passed; full suite 216 passed, 21 known/deferred structural failures, 2 skipped. Exact deferred nodeids are frozen in `PHASE8_STABILIZATION.md`. |
| 9 | `07f325e8fc682e7f99049428b04f1718c1b137cb` | — | Python compile PASS; Phase 9 focused tests PASS; full suite 217 passed, 21 known/deferred structural failures, 2 skipped; deferred nodeids unchanged from Phase 8; new regressions 0. |
| 10 | `027f22c95be3c856e78d3b6cec79a5b0bbd90daf` | — | `git diff --check` PASS; Python compile PASS; pollution-chain tests 4 passed; Phase 10 focused tests 9 passed; full suite 219 passed, 21 known/deferred structural failures, 2 skipped, 1 warning, 26 subtests passed; deferred nodeids unchanged; new regressions 0. |
| 11 | `ceb4063abb5e2d9b4b7167f0c361d239d983a4f2` | — | `git diff --check` PASS; Python compile PASS; `test_chat_router` 2 passed; Chat focused tests 17 passed with 1 known/deferred structural failure and 5 subtests passed; full suite 221 passed, 21 known/deferred structural failures, 2 skipped, 1 warning, 26 subtests passed; deferred nodeids unchanged; new regressions 0. |
| 12 | `eb3725356796c7ccb99a89c4b38a4cf6acfb6bda` | — | `git diff --check` PASS; Python compile PASS; `test_admin_router` 3 passed; top-level Admin APIRoutes / method-path keys / unique keys = 69 / 69 / 69; nested Admin APIRoutes = 0; Admin focused tests 29 passed; full suite 224 passed, 21 known/deferred structural failures, 2 skipped, 1 warning, 26 subtests passed; deferred nodeids unchanged; new regressions 0. |
| 13 | `b4b35ffd62bcae7836584e979e7739a7b2032b38` | — | `git diff --check` PASS; Python compile PASS; schema/Chat/Models/Projects/Repositories focused tests 35 passed with 4 subtests; canonical class identity PASS; OpenAPI exact equality PASS (SHA-256 `88d9cc927b96df5f4aca176836a8403403023bc6da911bfcf0e2f78b65c7e03d`, 46 paths, 70 components); full suite 228 passed, 21 known/deferred structural failures, 2 skipped, 1 warning, 26 subtests passed; deferred nodeids unchanged; new regressions 0. |
| 14 | `6d7642289058f06d8645807cdf2bcde3f2871970` | — | `git diff --check` PASS; composition/router focused tests 11 passed; `backend.app` module identity PASS; 82 method/path keys unique; legacy compatibility proxy PASS; OpenAPI exact equality PASS (SHA-256 `88d9cc927b96df5f4aca176836a8403403023bc6da911bfcf0e2f78b65c7e03d`, 46 paths, 70 components); full suite 230 passed, 21 known/deferred structural failures, 2 skipped, 1 warning, 26 subtests passed; deferred nodeids unchanged; new regressions 0. |
| 15 | `721214c6d02b364f7ee90ae9e4f88010dd98416e` | — | `git diff --check` PASS; Admin boundary tests 3 passed; TypeScript `tsc --noEmit` PASS; Vite production build PASS (17 modules); `main.ts` reduced from 2,462 to 2,162 lines; full suite 233 passed, 21 known/deferred structural failures, 2 skipped, 1 warning, 26 subtests passed; deferred nodeids unchanged; new regressions 0. |
| 16 | `dda08df4c145bd9eb76caa5231a277b8eff9e463` | — | `git diff --check` PASS; previously deferred structural suite 55 passed; Python compile PASS; Admin production build PASS; full suite 254 passed, 0 failed, 2 skipped, 1 warning, 26 subtests passed; all 21 frozen failures resolved by canonical-path alignment; assertion semantics preserved. |
