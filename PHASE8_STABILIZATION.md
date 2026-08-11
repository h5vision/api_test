# Phase 8 Stabilization

## Authority

- GitHub branch: `PoikileKerdo/api_test:Backend`
- Phase 8 implementation commit: `9cd356251a97534cf2b0110e412673ac3328deb5`
- Stabilization commit: pending
- Drive working-tree files are synchronized to the Phase 8 implementation plus the test fixes below.
- Local/Drive `.git/refs/heads/Backend` was synchronized through a normal Git fast-forward and now points to `9cd356251a97534cf2b0110e412673ac3328deb5`.
- The original working tree was preserved as `stash@{0}` with message `pre-phase8-stabilization-sync-2026-08-11`; no manual ref edit or hard reset was used.
- Do not advance to Phase 9 until the stabilization gate below is complete.

## Applied Drive synchronization

- Replaced `backend/generation.py` with the compatibility alias from the Phase 8 implementation commit.
- Populated `backend/domains/generation/` with the Phase 8 catalog, context, contracts, execution, orchestration, and routing modules.
- Synchronized `backend/integrations/ai_server/ollama.py` and `openai_compatible.py`.
- Updated Generation tests so HTTP mocks patch the new transport owners.

## Generation test ownership fixes

- OpenAI-compatible catalog HTTP: `backend.integrations.ai_server.openai_compatible.urllib.request.urlopen`
- Ollama streaming HTTP: `backend.integrations.ai_server.ollama.urllib.request.urlopen`

## Validation completed in the synchronization workspace

- Python compile: PASS for Phase 8 Generation and AI Server modules.
- Focused Generation/AI Server tests: `15 passed`.
- New Phase 8 functional regressions observed: `0` in the focused suite.

## Known/deferred full-suite baseline

The synchronized local full-suite result is `216 passed, 21 failed, 2 skipped, 1 warning, 26 subtests passed`. The four Phase 8 Generation failures are resolved. The remaining failures are frozen as the following known/deferred structural nodeids:

```text
tests/test_p2e_vector_index_chat_processing.py::test_vector_index_registry_uses_concrete_generation_selector
tests/test_p2e_vector_index_chat_processing.py::test_both_managed_index_paths_register_vector_index
tests/test_p2e_vector_index_chat_processing.py::test_provider_managed_chat_bypasses_project_resolution_and_rag
tests/test_p2f_generation_vector_index.py::test_generation_binding_is_immutable_and_completion_requires_provenance
tests/test_p2f_generation_vector_index.py::test_git_and_offline_indexing_bind_the_registered_vector_index
tests/test_p2f_generation_vector_index.py::test_retrieval_uses_project_vector_route_binding_not_runtime_collection
tests/test_p2f_generation_vector_index.py::test_agentic_rag_reuses_one_resolved_vector_route
tests/test_p2f_generation_vector_index.py::test_model_list_contract_exposes_catalog_revision
tests/test_p2g_snapshot_vector_bindings.py::test_both_managed_index_paths_register_snapshot_vector_binding
tests/test_p2g_snapshot_vector_bindings.py::test_build_completion_requires_binding_and_verifies_it_atomically_without_routing
tests/test_p2g_snapshot_vector_bindings.py::test_generation_failure_marks_unverified_binding_failed
tests/test_p2g_snapshot_vector_bindings.py::test_retrieval_requires_verified_active_route_binding_before_vector_target_resolution
tests/test_p2g_snapshot_vector_bindings.py::test_search_and_chat_expose_binding_provenance_for_p3
tests/test_p2h_external_vector_indexes.py::test_admin_workflow_separates_discover_attach_verify_and_snapshot_binding
tests/test_p2h_external_vector_indexes.py::test_reattach_preserves_existing_verification_and_external_binding_checks_tenant
tests/test_p2i_project_vector_routes.py::test_build_readiness_and_route_promotion_are_separate
tests/test_p2i_project_vector_routes.py::test_runtime_has_one_route_authority_and_no_active_generation_fallback
tests/test_p2i_project_vector_routes.py::test_admin_route_contract_uses_binding_id_and_optimistic_revision
tests/test_p2j_vector_federation_closure.py::test_p2j_runtime_rechecks_configured_tenant_and_locks_mutation_candidates
tests/test_p2j_vector_federation_closure.py::test_legacy_active_generation_is_not_a_runtime_authority
tests/test_p3c_chat_delivery.py::test_public_chat_source_contains_sse_and_context_contracts
```

These failures inspect legacy physical source paths and are deferred to Phase 16 test realignment. Any failure outside this exact list is a new regression and blocks the next phase.

## Phase 8 stabilization gate

1. Local Git checkout is synchronized to `Backend@9cd356251a97534cf2b0110e412673ac3328deb5` while preserving unrelated local work.
2. Phase 8 Generation/AI Server focused tests pass.
3. Full pytest is rerun.
4. Remaining failures are compared against and frozen as exact known/deferred structural nodeids.
5. No new functional regression remains.
6. A separate Phase 8 stabilization commit is created and its SHA replaces `pending` in `REFACTOR_PHASES.md` and this file.
7. Only then may Phase 9 start.
