from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from ipaddress import ip_address, ip_network
from time import perf_counter
from typing import Any, Callable
from urllib.parse import quote, unquote, urlsplit
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from .agentic_rag import AgenticRAGOrchestrator, ConversationAwareQueryPlanner
from .briefings import build_project_briefing_response
from .ai_providers import (
    AIProvider,
    AIProviderRegistry,
    AIProviderStoreError,
    PostgresAIProviderStore,
)
from .admin_snapshots import create_admin_snapshot_router
from .config import settings as bootstrap_settings
from .connectivity import ConnectivityStoreError, PostgresConnectivityStore
from .chat_contexts import ChatContextError, ChatContextRecord, ChatContextService
from .chat_progress import simulated_chat_progress
from .chat_streaming import wants_chat_sse
from .distributed import DistributedStateError, RedisCoordinator
from .frontend_clients import (
    FrontendClient,
    FrontendClientStoreError,
    PostgresFrontendClientStore,
)
from .chat_intake import (
    ChatIntakeSettingsError,
    PostgresChatIntakeSettingsStore,
    normalize_chat_intake,
    resolve_deep_normalization,
)
from .canonical_context import (
    CANONICAL_CONTEXT_SCHEMA_VERSION,
    CanonicalContext,
    CanonicalContextRetrieval,
    build_canonical_context,
)
from .generation import GenerationRouter
from .chat_routing import classify_chat_request, allows_unresolved_project_fallback
from .metadata_store import MetadataStoreError, PostgresMetadataStore
from .model_catalog import model_catalog_revision
from .model_access import (
    ModelAccessPolicyError,
    PostgresModelAccessPolicyStore,
)
from .offline_embeddings import (
    OfflineEmbeddingArtifactError,
    OfflineEmbeddingImporter,
)
from .local_projects import (
    LocalProjectError,
    LocalProjectRegistry,
    fingerprint_frontend_tree,
)
from .language_registry import LanguageDetectRequest, language_registry
from .project_store import PostgresProjectStore, ProjectStoreError
from .project_resolution import resolve_project_id
from .retrieval import AdaptiveReranker
from .rag_lab import RagLabClient, RagLabError
from .repository_indexer import RepositoryIndexer
from .repository_store import PostgresRepositoryStore, RepositoryStoreError
from .schema_guard import CURRENT_REVISION, BASELINE_TABLE_COLUMNS, inspect_schema
from .project_snapshots.contracts import (
    SnapshotImportRequest,
    SnapshotImportResponse,
    snapshot_fingerprint,
)
from .project_snapshots.repository import (
    PostgresSnapshotRepository,
    SnapshotRepositoryError,
)
from .project_snapshots.service import SnapshotService, SnapshotServiceError
from .snapshots.service import GithubSnapshotService
from .snapshot_compare import (
    SnapshotCompareRequest,
    SnapshotCompareResponse,
    SnapshotComparisonError,
    SnapshotComparisonService,
)
from .schemas import (
    ChatRequest,
    ChatResponse,
    ChatContextRegistrationRequest,
    ChatContextResponse,
    ChatIntakeSettingsResponse,
    ChatIntakeSettingsUpdateRequest,
    SourceDocument,
    BackendAIConnectivityStatus,
    ClientHeartbeatRequest,
    ClientHeartbeatResponse,
    ConnectivityStatusResponse,
    DocumentInput,
    APIEndpointActivity,
    APIEndpointActivityResponse,
    AICommunicationProbeRequest,
    AICommunicationProbeResponse,
    AIProviderListResponse,
    AIProviderRecord,
    AIProviderWriteRequest,
    ChatAuditLog,
    ChatAuditLogListResponse,
    ChatSessionListResponse,
    ChatSessionSummary,
    ChatSessionUser,
    CommunicationEvent,
    CommunicationEventListResponse,
    FrontendRegistrationEvent,
    FrontendRegistrationEventListResponse,
    FrontendConnectivityStatus,
    FrontendClientListResponse,
    FrontendClientRecord,
    FrontendClientWriteRequest,
    IngestRequest,
    IngestResponse,
    IndexedProjectItem,
    IndexedProjectListResponse,
    LegacyIngestRequest,
    LegacyQueryRequest,
    MetadataListResponse,
    MetadataRecord,
    MetadataScope,
    MetadataUpsertRequest,
    ModelListResponse,
    ModelAccessUpdateRequest,
    ModelAccessUpdateResponse,
    OllamaScanResponse,
    OllamaScanTarget,
    GitVersionInfo,
    ProjectVersionCheckRequest,
    ProjectVersionCheckResponse,
    ProjectVersionChecks,
    ProjectVersionDescriptor,
    NetworkEndpointSettings,
    NetworkSettingsResponse,
    NetworkSettingsUpdateRequest,
    OfflineEmbeddingArtifactListResponse,
    OfflineEmbeddingArtifactSummary,
    RuntimeServiceSettingsResponse,
    RuntimeServiceSettingsUpdateRequest,
    RuntimeSetupStatusResponse,
    RuntimeGroqSettingsResponse,
    RuntimeVectorSettingsResponse,
    ErrorResponse,
    APIError,
    API_SCHEMA_VERSION,
    ValidationIssue,
    ProjectMetadataIngestRequest,
    SearchRequest,
    SearchResponse,
    UploadCreateRequest,
    UploadManifestPageRequest,
    UploadProgressResponse,
    UploadSessionResponse,
    IndexingJobResponse,
    IndexingJobListResponse,
    IndexingJobSummary,
    ProjectFileResponse,
    ProjectBriefingResponse,
    ProjectTreeEntry,
    ProjectTreeResponse,
    RepositoryIndexJobResponse,
    RepositoryIndexRequest,
    RepositorySourceListResponse,
    RepositoryBrowserItem,
    RepositoryBrowserListResponse,
    RepositorySourceTreeResponse,
    RepositorySourceRecord,
    RepositorySourceWriteRequest,
    VectorIndexValidationResponse,
    VectorIndexRecordResponse,
    VectorIndexListResponse,
    ExternalVectorIndexDiscoveryItem,
    ExternalVectorIndexDiscoveryResponse,
    ExternalVectorIndexAttachRequest,
    ExternalVectorIndexVerifyRequest,
    ExternalVectorIndexVerificationResponse,
    ExternalVectorIndexAttachResponse,
    ExternalSnapshotVectorBindingVerifyRequest,
    SnapshotVectorBindingRecordResponse,
    SnapshotVectorBindingListResponse,
    VectorTargetWriteRequest,
    VectorTargetRecordResponse,
    VectorTargetListResponse,
    ProjectVectorRouteCandidateResponse,
    ProjectVectorRouteCandidateListResponse,
    ProjectVectorRouteRecordResponse,
    ProjectVectorRouteWriteRequest,
    ProjectVectorRouteClearRequest,
    ProjectVectorRouteEventResponse,
    ProjectVectorRouteEventListResponse,
    EmbeddingProfileWriteRequest,
    EmbeddingProfileRecordResponse,
    EmbeddingProfileListResponse,
)
from .services import ChatService, EmbeddingService, ServiceError
from .embedding_profiles import (
    EmbeddingProfileRecord,
    EmbeddingProfileStoreError,
    PostgresEmbeddingProfileStore,
)
from .runtime_authority import RuntimeSettingsProxy, RuntimeSettingsResolver
from .runtime_config import (
    PostgresRuntimeNetworkSettingsStore,
    RuntimeNetworkSettings,
    RuntimeNetworkSettingsError,
)
from .runtime_services import (
    PostgresRuntimeServiceSettingsStore,
    RuntimeServiceSettings,
    RuntimeServiceSettingsError,
)
from .text import chunk_text_with_metadata
from .uploads import UploadError, UploadManager
from .vector_gateway import (
    RuntimeVectorStore,
    build_vector_store,
    build_vector_store_for_index,
    settings_for_vector_index,
)
from .vector_contract import vector_service_contract
from .vector_store import (
    QdrantVectorAdapter,
    VectorIndexRef,
    VectorSelector,
    VectorStoreError,
)
from .vector_targets import PostgresVectorTargetStore, VectorTargetRecord, VectorTargetStoreError
from .vector_indexes import PostgresVectorIndexStore, VectorIndexRecord, VectorIndexStoreError
from .external_vector_indexes import (
    ExternalVectorIndexVerificationRecord,
    ExternalVectorIndexVerificationStoreError,
    PostgresExternalVectorIndexVerificationStore,
    evaluate_external_index_probe,
    evaluate_external_snapshot_probe,
    sample_payload_keys,
)
from .snapshot_vector_bindings import (
    PostgresSnapshotVectorBindingStore,
    SnapshotVectorBindingRecord,
    SnapshotVectorBindingStoreError,
)
from .project_vector_routes import (
    PostgresProjectVectorRouteStore,
    ProjectVectorRouteConflict,
    ProjectVectorRouteRecord,
    ProjectVectorRouteStoreError,
    RouteCandidateContext,
)


# Deployment/bootstrap configuration is available before PostgreSQL can be read.
# Runtime model/vector routing is replaced below by the administrator authority.
settings = bootstrap_settings

app = FastAPI(
    title="VS Code AI Assistant Backend",
    version="3.0.0",
    description=(
        "Frozen public API v1 for VS Code repository ingest, configurable vector retrieval, "
        "and replaceable model-runtime generation. Canonical schema version: 1.0."
    ),
    openapi_tags=[
        {"name": "System", "description": "Health, model discovery, and connectivity."},
        {"name": "Chat", "description": "Synchronous RAG chat contract used by VS Code."},
        {"name": "Documents", "description": "Repository registration and indexing."},
        {"name": "Projects", "description": "Indexed project discovery and versions."},
        {"name": "Uploads", "description": "Resumable large repository uploads."},
    ],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin for origin in settings.cors_origins if "*" not in origin],
    allow_origin_regex=r"^(vscode-webview://.*|https?://(127\.0\.0\.1|localhost)(:\d+)?)$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "X-Request-ID",
        "X-API-Version",
        "X-Client-ID",
        "X-Client-Auto-Registered",
        "X-Vision-Chat-Transport",
    ],
)

logger = logging.getLogger(__name__)
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
ERROR_RESPONSES = {
    422: {"model": ErrorResponse, "description": "Request contract validation failed."},
    429: {"description": "Edge rate limit exceeded; the body may be non-JSON."},
    500: {"model": ErrorResponse, "description": "Unexpected internal API failure."},
    502: {"model": ErrorResponse, "description": "Embedding or generation upstream failed."},
    503: {"model": ErrorResponse, "description": "A required backend service is unavailable."},
    504: {"model": ErrorResponse, "description": "An upstream provider timed out."},
}
MONITORED_FRONTEND_ENDPOINTS = (
    ("GET", "/v1/health"),
    ("GET", "/v1/models"),
    ("GET", "/v1/languages"),
    ("POST", "/v1/languages/detect"),
    ("GET", "/v1/IngestResponse"),
    ("GET", "/v1/repositories"),
    ("GET", "/v1/repositories/{source_id}/tree"),
    ("GET", "/v1/projects/{project_id}/tree"),
    ("GET", "/v1/projects/{project_id}/briefing"),
    ("GET", "/v1/briefing"),
    ("GET", "/v1/indexing-jobs"),
    ("POST", "/v1/client-heartbeat"),
    ("POST", "/v1/documents/ingest"),
    ("POST", "/v1/projects/{project_id}/version/check"),
    ("POST", "/v1/snapshots/compare"),
    ("POST", "/v1/chat"),
    ("POST", "/v1/chat/contexts"),
)


def _frontend_source_ip(request: Request) -> str:
    peer = request.client.host if request.client is not None else ""
    try:
        peer_address = ip_address(peer)
    except ValueError:
        return peer or "unknown"
    trusted = False
    for value in settings.trusted_proxy_hosts:
        try:
            if peer_address in ip_network(value, strict=False):
                trusted = True
                break
        except ValueError:
            continue
    if not trusted:
        return peer
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
    if not forwarded:
        return peer
    try:
        forwarded_address = ip_address(forwarded)
    except ValueError:
        return peer
    if (
        forwarded_address.is_unspecified
        or forwarded_address.is_multicast
    ):
        return peer
    return str(forwarded_address)


def _normalize_activity_path(path: str) -> str:
    if re.fullmatch(r"/v1/projects/[^/]+/version/check", path):
        return "/v1/projects/{project_id}/version/check"
    if re.fullmatch(r"/v1/repositories/[^/]+/tree", path):
        return "/v1/repositories/{source_id}/tree"
    if re.fullmatch(r"/v1/projects/.+/tree", path):
        return "/v1/projects/{project_id}/tree"
    if re.fullmatch(r"/v1/projects/.+/briefing", path):
        return "/v1/projects/{project_id}/briefing"
    return path


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else f"req_{uuid4().hex}"


def _error_code(status_code: int) -> str:
    return {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        413: "PAYLOAD_TOO_LARGE",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMITED",
        500: "INTERNAL_ERROR",
        501: "NOT_IMPLEMENTED",
        502: "UPSTREAM_ERROR",
        503: "SERVICE_UNAVAILABLE",
        504: "UPSTREAM_TIMEOUT",
    }.get(status_code, "REQUEST_FAILED")


def _error_response(
    request: Request,
    status_code: int,
    detail: str | list[dict[str, Any]],
    *,
    code: str | None = None,
    issues: list[ValidationIssue] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    message = detail if isinstance(detail, str) else "Request validation failed"
    request_id = _request_id(request)
    error_code = code or _error_code(status_code)
    retryable = status_code in {429, 502, 503, 504}
    chat_compatibility = request.url.path.rstrip("/") == "/v1/chat"
    payload = ErrorResponse(
        request_id=request_id,
        detail=detail,
        error=APIError(
            code=error_code,
            message=message,
            retryable=retryable,
            issues=issues or [],
        ),
    )
    content = payload.model_dump(mode="json")
    if chat_compatibility:
        # Team-vision's current APIService.ts does not inspect response.ok and
        # casts every parsed /v1/chat body to ChatResponse. Keep the frozen
        # ErrorResponse schema unchanged while supplying the runtime fields
        # that prevent stream.markdown(undefined).
        content.update(
            {
                "answer": (
                    "❌ Chat 요청을 처리하지 못했습니다: "
                    f"{message} (HTTP {status_code}, request_id: {request_id})"
                ),
                "source": [],
                "metadata": {
                    "schema_version": API_SCHEMA_VERSION,
                    "request_id": request_id,
                    "status": "failed",
                    "http_status": status_code,
                    "error_code": error_code,
                    "retryable": retryable,
                },
            }
        )
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers=headers,
    )


@app.middleware("http")
async def request_context(request: Request, call_next: Any) -> Any:
    started_at = perf_counter()
    supplied = (request.headers.get("x-request-id") or "").strip()
    request.state.request_id = (
        supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else f"req_{uuid4().hex}"
    )
    path = request.url.path
    normalized_activity_path = _normalize_activity_path(path)
    client_type = (request.headers.get("x-client-type") or "").strip().lower()
    client_id = (request.headers.get("x-client-id") or "").strip()
    instance_id = (
        request.headers.get("x-client-instance-id") or ""
    ).strip()
    auto_enrollment_request = (
        request.method == "POST"
        and path.rstrip("/")
        in {"/v1/chat", "/v1/chat/contexts", "/v1/snapshots/compare"}
        and client_type not in {"admin-dashboard", "admin-playground"}
    )
    dashboard_request = client_type in {"admin-dashboard", "admin-playground"}
    managed_frontend_request = (
        not dashboard_request
        and (
            bool(client_id)
            or bool(instance_id)
            or client_type == "vscode-extension"
            or auto_enrollment_request
        )
    )
    monitored_frontend_endpoint = (
        request.method,
        normalized_activity_path,
    ) in MONITORED_FRONTEND_ENDPOINTS
    source_host = _frontend_source_ip(request)
    client_name = (
        request.headers.get("x-client-name")
        or request.headers.get("user-agent")
        or ""
    ).strip()[:80] or None
    declared_user = unquote(
        (request.headers.get("x-client-user") or "").strip()
    ).strip()[:120] or None
    client_version = (
        request.headers.get("x-client-version") or ""
    ).strip()[:100] or None
    trace_initial_registration = auto_enrollment_request and not bool(client_id)
    telemetry_client_id = (
        client_id[:255]
        or (
            f"vscode:{source_host}"
            if managed_frontend_request
            else f"public:{source_host}"
        )[:255]
    )
    request.state.frontend_client_id = None
    request.state.frontend_client_auto_registered = False
    request.state.chat_deep_normalization_mode = "inherit"
    metrics_started = False
    if path != "/v1/admin/runtime-metrics":
        try:
            await run_in_threadpool(
                redis_coordinator.request_started,
                normalized_activity_path,
            )
            metrics_started = True
        except DistributedStateError:
            metrics_started = False

    async def finalize_response(response: Any) -> Any:
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-API-Version"] = API_SCHEMA_VERSION
        response.headers["X-Backend-Instance"] = settings.instance_id
        if request.state.frontend_client_id:
            response.headers["X-Client-ID"] = request.state.frontend_client_id
            response.headers["X-Client-Auto-Registered"] = (
                "true"
                if request.state.frontend_client_auto_registered
                else "false"
            )
        background = BackgroundTasks()
        if response.background is not None:
            if isinstance(response.background, BackgroundTasks):
                background.tasks.extend(response.background.tasks)
            else:
                background.tasks.append(response.background)
        if metrics_started:
            background.add_task(_safe_finish_request_metric)
        if (
            (
                managed_frontend_request
                or (monitored_frontend_endpoint and not dashboard_request)
            )
            and path.startswith("/v1/")
            and not path.startswith("/v1/admin/")
            and request.method != "OPTIONS"
        ):
            background.add_task(
                _safe_record_api_activity,
                client_id=telemetry_client_id,
                method=request.method,
                path=normalized_activity_path,
                status_code=response.status_code,
                duration_ms=round((perf_counter() - started_at) * 1000),
                request_id=request.state.request_id,
            )
            if managed_frontend_request or normalized_activity_path != "/v1/health":
                background.add_task(
                    _safe_record_communication_event,
                    request_id=request.state.request_id,
                    channel=(
                        "frontend-fastapi"
                        if managed_frontend_request
                        else "public-fastapi"
                    ),
                    direction=(
                        "frontend_to_fastapi"
                        if managed_frontend_request
                        else "public_client_to_fastapi"
                    ),
                    phase="http.exchange",
                    status=(
                        "success"
                        if 200 <= int(response.status_code) < 300
                        else "error"
                    ),
                    method=request.method,
                    path=normalized_activity_path,
                    client_id=telemetry_client_id,
                    status_code=response.status_code,
                    duration_ms=round((perf_counter() - started_at) * 1000),
                    details={
                        "request_received": True,
                        "response_sent": True,
                        "client_type": client_type or "unclassified",
                    },
                )
        if background.tasks:
            response.background = background
        return response

    guard_exempt = (
        request.method == "OPTIONS"
        or path in {"/", "/v1/health", "/v1/live", "/v1/ready", "/openapi.json", "/docs", "/redoc"}
        or path.startswith("/v1/admin/")
    )
    if not guard_exempt:
        setup_state = runtime_settings_resolver.setup_state(refresh=False)
        if not setup_state.configured:
            return await finalize_response(
                _error_response(
                    request,
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Administrator runtime configuration is required: "
                    + ", ".join(setup_state.missing),
                    code="RUNTIME_CONFIGURATION_REQUIRED",
                )
            )

    if managed_frontend_request and not guard_exempt:
        if trace_initial_registration:
            await run_in_threadpool(
                _safe_record_frontend_registration_event,
                request_id=request.state.request_id,
                event_type="registration_attempt",
                status="started",
                instance_id=instance_id or None,
                client_name=client_name,
                declared_user=declared_user,
                client_version=client_version,
                source_ip=source_host,
                identification_method=(
                    "instance_id" if instance_id else "legacy_source_ip"
                ),
                reason=f"POST {path.rstrip('/')} arrived without X-Client-ID",
            )
        try:
            decision = await run_in_threadpool(
                frontend_client_store.authorize_or_register,
                client_id=client_id or None,
                instance_id=instance_id or None,
                source_ip=source_host,
                auto_register=auto_enrollment_request,
                client_name=client_name,
            )
        except FrontendClientStoreError:
            if trace_initial_registration:
                await run_in_threadpool(
                    _safe_record_frontend_registration_event,
                    request_id=request.state.request_id,
                    event_type="registration_failed",
                    status="error",
                    instance_id=instance_id or None,
                    client_name=client_name,
                    declared_user=declared_user,
                    client_version=client_version,
                    source_ip=source_host,
                    identification_method=(
                        "instance_id" if instance_id else "legacy_source_ip"
                    ),
                    reason="Frontend client access registry is unavailable",
                )
            return await finalize_response(
                _error_response(
                    request,
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Frontend client access registry is unavailable",
                )
            )
        if not decision.allowed:
            if trace_initial_registration:
                await run_in_threadpool(
                    _safe_record_frontend_registration_event,
                    request_id=request.state.request_id,
                    event_type="registration_denied",
                    status="denied",
                    client_id=(
                        decision.client.client_id
                        if decision.client is not None
                        else None
                    ),
                    instance_id=(
                        decision.client.instance_id
                        if decision.client is not None
                        else instance_id or None
                    ),
                    client_name=(
                        decision.client.name
                        if decision.client is not None
                        else client_name
                    ),
                    declared_user=declared_user,
                    client_version=client_version,
                    source_ip=source_host,
                    registration_type=(
                        decision.client.registration_type
                        if decision.client is not None
                        else None
                    ),
                    identification_method=decision.reason,
                    reason=decision.reason,
                )
            return await finalize_response(
                _error_response(
                    request,
                    status.HTTP_403_FORBIDDEN,
                    decision.reason,
                )
            )
        if decision.client is not None:
            request.state.frontend_client_id = decision.client.client_id
            request.state.chat_deep_normalization_mode = (
                decision.client.chat_deep_normalization_mode
            )
            request.state.frontend_client_auto_registered = (
                decision.auto_registered
            )
            telemetry_client_id = decision.client.client_id
        if trace_initial_registration:
            await run_in_threadpool(
                _safe_record_frontend_registration_event,
                request_id=request.state.request_id,
                event_type=(
                    "client_id_issued"
                    if decision.auto_registered
                    else "client_recognized"
                ),
                status="success",
                client_id=(
                    decision.client.client_id
                    if decision.client is not None
                    else None
                ),
                instance_id=(
                    decision.client.instance_id
                    if decision.client is not None
                    else instance_id or None
                ),
                client_name=(
                    decision.client.name
                    if decision.client is not None
                    else client_name
                ),
                declared_user=declared_user,
                client_version=client_version,
                source_ip=source_host,
                registration_type=(
                    decision.client.registration_type
                    if decision.client is not None
                    else None
                ),
                identification_method=decision.reason,
                is_first_connection=decision.auto_registered,
                reason=(
                    "Server generated and returned X-Client-ID"
                    if decision.auto_registered
                    else "Existing Client matched before Chat processing"
                ),
            )
    response = await call_next(request)
    return await finalize_response(response)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if not isinstance(detail, (str, list)):
        detail = str(detail)
    return _error_response(
        request,
        exc.status_code,
        detail,
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    issues = [
        ValidationIssue(
            location=[str(item) if not isinstance(item, int) else item for item in error["loc"]],
            message=error["msg"],
            type=error["type"],
        )
        for error in exc.errors()
    ]
    detail = [
        {
            "loc": issue.location,
            "msg": issue.message,
            "type": issue.type,
        }
        for issue in issues
    ]
    return _error_response(
        request,
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail,
        issues=issues,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled API error request_id=%s", _request_id(request), exc_info=exc)
    return _error_response(
        request,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "Internal server error",
    )

runtime_service_store = PostgresRuntimeServiceSettingsStore(bootstrap_settings)
runtime_network_store = PostgresRuntimeNetworkSettingsStore(bootstrap_settings)
vector_target_store = PostgresVectorTargetStore(bootstrap_settings)
embedding_profile_store = PostgresEmbeddingProfileStore(bootstrap_settings)
vector_index_store = PostgresVectorIndexStore(bootstrap_settings)
external_vector_index_verification_store = PostgresExternalVectorIndexVerificationStore(bootstrap_settings)
snapshot_vector_binding_store = PostgresSnapshotVectorBindingStore(bootstrap_settings)
project_vector_route_store = PostgresProjectVectorRouteStore(bootstrap_settings)
runtime_settings_resolver = RuntimeSettingsResolver(
    bootstrap_settings,
    runtime_service_store,
    runtime_network_store,
    vector_target_store,
    embedding_profile_store,
)
settings = RuntimeSettingsProxy(runtime_settings_resolver)
embedding_service = EmbeddingService(settings)
chat_service = ChatService(settings)
rag_lab_client = RagLabClient(
    bootstrap_settings.rag_lab_base_url,
    bootstrap_settings.rag_lab_token,
    bootstrap_settings.rag_lab_timeout_seconds,
)
rag_reranker = AdaptiveReranker(
    min_sources=settings.rag_min_sources,
    max_sources=settings.rag_max_sources,
    max_context_chars=settings.rag_context_max_chars,
    min_score=settings.rag_min_score,
    score_window=settings.rag_score_window,
)
agentic_query_planner = ConversationAwareQueryPlanner(
    balanced_steps=settings.agentic_rag_balanced_steps,
    deep_steps=settings.agentic_rag_deep_steps,
)
agentic_rag = AgenticRAGOrchestrator(
    rag_reranker,
    min_evidence=settings.rag_min_sources,
    min_coverage=settings.agentic_rag_min_coverage,
    min_novelty_ratio=settings.agentic_rag_min_novelty_ratio,
)
model_access_store = PostgresModelAccessPolicyStore(bootstrap_settings)
ai_provider_store = PostgresAIProviderStore(bootstrap_settings)
ai_provider_registry = AIProviderRegistry(
    ai_provider_store,
    bootstrap_settings,
    model_access_store.is_enabled,
)
generation_router = GenerationRouter(
    settings,
    backendai_base_url_provider=runtime_network_store.backendai_base_url,
    groq_settings_provider=runtime_service_store.groq_settings,
    model_enabled_provider=model_access_store.is_enabled,
    custom_provider_registry=ai_provider_registry,
)
vector_store = RuntimeVectorStore(runtime_settings_resolver.current)
metadata_store = PostgresMetadataStore(settings)
project_store = PostgresProjectStore(settings)
repository_store = PostgresRepositoryStore(settings)
snapshot_repository = PostgresSnapshotRepository(settings)
snapshot_service = SnapshotService(settings, repository=snapshot_repository)
github_snapshot_service = GithubSnapshotService(bootstrap_settings)
snapshot_comparison_service = SnapshotComparisonService(
    github_snapshot_service,
    repository_store,
)
offline_embedding_importer = OfflineEmbeddingImporter(
    settings,
    repository_store,
    vector_store,
    vector_index_store,
    snapshot_vector_binding_store,
    project_vector_route_store,
)
local_project_registry = LocalProjectRegistry(bootstrap_settings.project_db_local_root)
redis_coordinator = RedisCoordinator(bootstrap_settings)
chat_context_service = ChatContextService(redis_coordinator, repository_store)
upload_manager = UploadManager(
    bootstrap_settings,
    lock_factory=redis_coordinator.lock,
)
connectivity_store = PostgresConnectivityStore(settings)
frontend_client_store = PostgresFrontendClientStore(settings)
chat_intake_settings_store = PostgresChatIntakeSettingsStore(settings)
repository_indexer = RepositoryIndexer(
    settings,
    repository_store,
    embedding_service,
    vector_store,
    vector_index_store,
    snapshot_vector_binding_store,
    project_vector_route_store,
)


def current_runtime_settings(*, force: bool = False):
    """Return an immutable snapshot of the current administrator-owned runtime config."""
    return runtime_settings_resolver.current(force=force)


def repository_indexer_for_current_runtime() -> RepositoryIndexer:
    """Freeze one runtime profile for the lifetime of a single index generation."""
    snapshot = current_runtime_settings(force=True)
    store = PostgresRepositoryStore(snapshot)
    return RepositoryIndexer(
        snapshot,
        store,
        EmbeddingService(snapshot),
        build_vector_store(snapshot),
        PostgresVectorIndexStore(snapshot),
        PostgresSnapshotVectorBindingStore(snapshot),
        PostgresProjectVectorRouteStore(snapshot),
    )


def offline_embedding_importer_for_current_runtime() -> OfflineEmbeddingImporter:
    """Freeze one runtime profile for a single offline import job."""
    snapshot = current_runtime_settings(force=True)
    store = PostgresRepositoryStore(snapshot)
    return OfflineEmbeddingImporter(
        snapshot, store, build_vector_store(snapshot), PostgresVectorIndexStore(snapshot),
        PostgresSnapshotVectorBindingStore(snapshot), PostgresProjectVectorRouteStore(snapshot)
    )


def _safe_finish_request_metric() -> None:
    try:
        redis_coordinator.request_finished()
    except DistributedStateError:
        return


def _enqueue_worker_task(
    kind: str,
    payload: dict[str, Any],
    *,
    dedupe_key: str,
    repository_job_id: str | None = None,
) -> None:
    try:
        redis_coordinator.enqueue(
            kind,
            payload,
            dedupe_key=dedupe_key,
        )
    except DistributedStateError as exc:
        if repository_job_id:
            try:
                repository_store.update_job(
                    repository_job_id,
                    status="failed",
                    stage="queue_unavailable",
                    error="Redis indexing queue was unavailable before dispatch",
                    completed_at=datetime.now(timezone.utc),
                )
            except RepositoryStoreError:
                logger.exception(
                    "Failed to mark undispatched job as failed job_id=%s",
                    repository_job_id,
                )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis indexing queue is unavailable",
        ) from exc


def _network_settings_response(
    value: RuntimeNetworkSettings | None,
) -> NetworkSettingsResponse:
    if value is None:
        return NetworkSettingsResponse(
            configured=False,
            setup_required=True,
            frontend={
                "ip": bootstrap_settings.frontend_host,
                "port": bootstrap_settings.frontend_port,
            },
            backendai={"ip": "", "port": 0},
            updated_at=None,
            frontend_reachable=False,
            frontend_latency_ms=0,
            frontend_error="not_configured",
        )
    frontend_probe = runtime_network_store.probe_frontend()
    return NetworkSettingsResponse(
        configured=True,
        setup_required=False,
        frontend={"ip": value.frontend.ip, "port": value.frontend.port},
        backendai={"ip": value.backendai.ip, "port": value.backendai.port},
        updated_at=value.updated_at,
        frontend_reachable=frontend_probe["reachable"],
        frontend_latency_ms=frontend_probe["latency_ms"],
        frontend_error=frontend_probe["error"],
    )


def _runtime_service_settings_response(
    value: RuntimeServiceSettings | None,
) -> RuntimeServiceSettingsResponse:
    if value is None:
        return RuntimeServiceSettingsResponse(
            configured=False,
            setup_required=True,
            missing=[
                "default_model_id",
                "vector_target",
                "embedding_profile",
                "vector_index_settings",
            ],
            groq=RuntimeGroqSettingsResponse(
                enabled=False,
                base_url="",
                model="",
                public_model_id=bootstrap_settings.groq_public_model_id,
                api_key_configured=bool(bootstrap_settings.groq_api_key),
            ),
            default_model_id="",
            vector=RuntimeVectorSettingsResponse(provider="qdrant"),
            updated_at=None,
        )

    target = None
    if value.vector.vector_target_id:
        try:
            target = vector_target_store.get(value.vector.vector_target_id)
        except VectorTargetStoreError:
            target = None

    profile = None
    if value.vector.embedding_profile_id:
        try:
            profile = embedding_profile_store.get(value.vector.embedding_profile_id)
        except EmbeddingProfileStoreError:
            profile = None

    active = runtime_settings_resolver.current(force=True)
    active_vector_url = urlsplit(target.endpoint if target is not None else "")
    active_host = active_vector_url.hostname or ""
    active_port = active_vector_url.port or (
        443 if active_vector_url.scheme == "https" else (80 if active_vector_url.scheme == "http" else 0)
    )

    vector_reindex_required = False
    try:
        for project in project_store.list_projects():
            if str(project.get("index_status") or "").lower() not in {"ready", "active"}:
                continue
            if profile is None or (
                str(project.get("embedding_model_id") or project.get("embedding_model") or "")
                != profile.model_id
                or str(project.get("index_version") or "") != value.vector.index_version
            ):
                vector_reindex_required = True
                break
    except (ProjectStoreError, NameError):
        vector_reindex_required = False

    missing: list[str] = []
    if target is None or not target.enabled:
        missing.append("vector_target")
    if profile is None or not profile.enabled:
        missing.append("embedding_profile")
    if not value.groq.default_model_id:
        missing.append("default_model_id")
    if not (value.vector.collection and value.vector.index_version):
        missing.append("vector_index_settings")
    configured = not missing

    return RuntimeServiceSettingsResponse(
        configured=configured,
        setup_required=not configured,
        missing=missing,
        groq=RuntimeGroqSettingsResponse(
            enabled=value.groq.enabled,
            base_url=value.groq.base_url,
            model=value.groq.model,
            public_model_id=active.groq_public_model_id,
            api_key_configured=bool(active.groq_api_key),
        ),
        default_model_id=value.groq.default_model_id,
        vector=RuntimeVectorSettingsResponse(
            provider=(target.engine if target is not None else "qdrant"),
            vector_target_id=value.vector.vector_target_id,
            embedding_profile_id=value.vector.embedding_profile_id,
            host=active_host,
            port=active_port,
            collection=value.vector.collection,
            embedding_deployment=(profile.deployment if profile is not None else ""),
            embedding_provider=(profile.provider if profile is not None else ""),
            embedding_base_url=(profile.base_url if profile is not None else ""),
            embedding_model=(profile.model if profile is not None else ""),
            embedding_model_id=(profile.model_id if profile is not None else ""),
            embedding_dimension=(profile.dimension if profile is not None else 0),
            embedding_batch_size=(profile.batch_size if profile is not None else 0),
            index_version=value.vector.index_version,
            active_host=active_host,
            active_port=active_port,
            active_collection=active.qdrant_collection,
            active_embedding_deployment=active.embedding_deployment,
            active_embedding_provider=active.embedding_provider,
            active_embedding_base_url=active.embedding_base_url,
            active_embedding_model=active.embedding_model,
            active_embedding_model_id=active.embedding_model_id,
            active_embedding_dimension=active.embedding_dimension,
            active_embedding_batch_size=active.embedding_batch_size,
            active_index_version=active.index_version,
            restart_required=False,
            reindex_required=vector_reindex_required,
        ),
        updated_at=value.updated_at,
    )


def _frontend_client_record(
    client: FrontendClient,
    probe: dict[str, Any] | None = None,
) -> FrontendClientRecord:
    resolved_probe = probe or frontend_client_store.connection_status(client)
    return FrontendClientRecord(
        client_id=client.client_id,
        instance_id=client.instance_id,
        name=client.name,
        ip=client.ip,
        port=client.port,
        enabled=client.enabled,
        chat_deep_normalization_mode=client.chat_deep_normalization_mode,
        registration_type=client.registration_type,
        last_seen_ip=client.last_seen_ip,
        last_seen_at=client.last_seen_at,
        reachable=bool(resolved_probe["reachable"]),
        latency_ms=int(resolved_probe["latency_ms"]),
        error=resolved_probe["error"],
        created_at=client.created_at,
        updated_at=client.updated_at,
    )


def _frontend_client_store_error(exc: FrontendClientStoreError) -> HTTPException:
    conflict = "already registered" in str(exc).lower()
    return HTTPException(
        status_code=(
            status.HTTP_409_CONFLICT
            if conflict
            else status.HTTP_503_SERVICE_UNAVAILABLE
        ),
        detail=str(exc),
    )


def _chat_intake_settings_error(exc: ChatIntakeSettingsError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=str(exc),
    )


PERSISTENCE_CAPABILITY_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "id": "source_history",
        "role": "소스 · Snapshot 원장",
        "description": "Repository 등록, immutable Snapshot, 파일 엔트리의 기준 이력을 보존합니다.",
        "tables": ("repository_sources", "project_snapshots", "snapshot_entries"),
    },
    {
        "id": "index_provenance",
        "role": "인덱싱 · 검색 근거 이력",
        "description": "인덱싱 세대, chunk provenance, 작업 진행 상태와 기존 vector mapping을 보존합니다.",
        "tables": (
            "index_generations", "generation_chunks", "repository_index_jobs",
            "document_versions", "document_chunks", "vector_mappings",
        ),
    },
    {
        "id": "runtime_routing",
        "role": "실행 · 라우팅 설정",
        "description": "모델 실행 선택, VectorTarget registry, 임베딩/인덱스 전환 설정과 네트워크 기준을 보존합니다.",
        "tables": ("vector_targets", "runtime_service_settings", "runtime_network_settings"),
    },
    {
        "id": "model_gateway",
        "role": "모델 연결 · 접근 정책",
        "description": "교체 가능한 모델 실행 연결, 발견된 모델과 관리자 사용 정책을 보존합니다.",
        "tables": ("ai_provider_configs", "ai_provider_models", "model_access_policies"),
    },
    {
        "id": "client_access",
        "role": "클라이언트 접근 · 연결 상태",
        "description": "Frontend 등록, 접근 허용 여부, heartbeat와 API 활동 상태를 보존합니다.",
        "tables": ("frontend_clients", "client_connections", "frontend_api_activity"),
    },
    {
        "id": "audit_history",
        "role": "통신 · 대화 감사 이력",
        "description": "요청/응답 경로, 대화 결과와 최초 등록 과정을 관리자 감사 이력으로 보존합니다.",
        "tables": ("communication_events", "chat_audit_logs", "frontend_registration_events"),
    },
    {
        "id": "project_metadata",
        "role": "프로젝트 메타데이터",
        "description": "Frontend가 전달한 프로젝트/문서 메타데이터와 문서 참조를 보존합니다.",
        "tables": ("frontend_metadata", "frontend_documents", "projects"),
    },
)


def _require_admin_proxy(request: Request) -> None:
    source_ip = request.client.host if request.client is not None else ""
    local_development = (
        source_ip in {"127.0.0.1", "::1"}
        and settings.backend_host in {"127.0.0.1", "localhost"}
    )
    if (
        request.headers.get("x-vision-admin-proxy") != "dashboard-internal"
        or (source_ip != settings.admin_proxy_ip and not local_development)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This administrator operation is only available through the dashboard",
        )


# The Snapshot dashboard consumes the GitHub Commit Control Plane contract.
# Keep the older project-snapshot diagnostics on a separate legacy path below.
app.include_router(
    create_admin_snapshot_router(
        bootstrap_settings,
        _require_admin_proxy,
        service_factory=lambda: github_snapshot_service,
    )
)


def _service_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=getattr(exc, "status_code", 503), detail=str(exc)
    )


def _metadata_store_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="PostgreSQL metadata store is unavailable",
    )


def _project_store_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="PostgreSQL project store is unavailable",
    )


def _repository_store_error(exc: Exception | None = None) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=str(exc) if exc else "PostgreSQL repository store is unavailable",
    )


def _repository_job_response(row: dict[str, Any]) -> RepositoryIndexJobResponse:
    return RepositoryIndexJobResponse(
        **row,
        status_url=f"/v1/indexing-jobs/{row['job_id']}",
    )


def _repository_job_summary(row: dict[str, Any]) -> IndexingJobSummary:
    state = str(row["status"])
    files_total = max(0, int(row.get("files_total") or 0))
    files_processed = max(0, int(row.get("files_processed") or 0))
    terminal = state in {"completed", "failed", "cancelled"}
    paused = state == "paused"
    stalled = (
        not terminal
        and not paused
        and (
            datetime.now(timezone.utc) - row["updated_at"]
        ).total_seconds() > 15 * 60
    )
    if state == "completed":
        progress = 100.0
    elif state == "queued":
        progress = 0.0
    elif state == "inspecting":
        progress = 5.0
    elif state == "snapshotting":
        progress = 15.0
    elif state == "chunking":
        progress = 25.0
    elif state == "publishing":
        progress = 95.0
    elif files_total > 0:
        progress = 25.0 + min(1.0, files_processed / files_total) * 65.0
    else:
        progress = 25.0 if state == "embedding" else 0.0
    return IndexingJobSummary(
        job_id=row["job_id"],
        job_kind="repository",
        project_id=row["project_id"],
        source_id=row.get("source_id"),
        state=state,
        stage=str(row.get("stage") or state),
        active=not terminal and not stalled and not paused,
        stalled=stalled,
        progress_percent=round(progress, 1),
        processed=files_processed,
        total=files_total,
        files_processed=files_processed,
        files_total=files_total,
        chunks_stored=max(0, int(row.get("chunks_stored") or 0)),
        bytes_total=max(0, int(row.get("bytes_total") or 0)),
        error=row.get("error"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row.get("completed_at"),
        status_url=f"/v1/indexing-jobs/{row['job_id']}",
    )


def _upload_job_summary(state: dict[str, Any]) -> IndexingJobSummary:
    job_state = str(state.get("status") or "unknown")
    files_total = max(0, int(state.get("document_count") or 0))
    files_processed = max(0, int(state.get("documents_processed") or 0))
    bytes_total = max(0, int(state.get("total_bytes") or 0))
    bytes_processed = max(0, int(state.get("bytes_received") or 0))
    terminal = job_state in {"completed", "failed", "cancelled"}
    updated_at = datetime.fromisoformat(
        str(state.get("updated_at") or state["created_at"])
    )
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    stalled = (
        not terminal
        and (datetime.now(timezone.utc) - updated_at).total_seconds() > 15 * 60
    )
    if job_state == "completed":
        progress = 100.0
    elif job_state in {"created", "uploading"}:
        progress = (
            min(1.0, bytes_processed / bytes_total) * 20.0
            if bytes_total > 0
            else 0.0
        )
    elif job_state == "queued":
        progress = 20.0
    elif files_total > 0:
        progress = 20.0 + min(1.0, files_processed / files_total) * 80.0
    else:
        progress = 20.0 if job_state == "indexing" else 0.0
    return IndexingJobSummary(
        job_id=str(state["job_id"]),
        job_kind="upload",
        project_id=str(state["project_id"]),
        upload_id=str(state["upload_id"]),
        state=job_state,
        stage=job_state,
        active=not terminal and not stalled,
        stalled=stalled,
        progress_percent=round(progress, 1),
        processed=files_processed,
        total=files_total,
        files_processed=files_processed,
        files_total=files_total,
        chunks_stored=max(0, int(state.get("chunks_stored") or 0)),
        bytes_processed=bytes_processed,
        bytes_total=bytes_total,
        error=state.get("error"),
        created_at=state["created_at"],
        updated_at=state.get("updated_at") or state["created_at"],
        completed_at=state.get("completed_at"),
        status_url=f"/v1/indexing-jobs/{state['job_id']}",
    )


def _upload_error(exc: UploadError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _frontend_client_id(request: Request, project_id: str) -> str | None:
    registered = getattr(request.state, "frontend_client_id", None)
    if isinstance(registered, str) and registered.strip():
        return registered.strip()[:255]
    supplied = (request.headers.get("x-client-id") or "").strip()
    if supplied:
        return supplied[:255]
    client_type = (request.headers.get("x-client-type") or "").strip().lower()
    if client_type == "vscode-extension":
        return f"vscode:{project_id}"[:255]
    return None


def _chat_owner_id(request: Request) -> str:
    """Stable owner key for cross-request Context and citation isolation."""

    client_id = _frontend_client_id(request, "__unscoped__")
    if client_id:
        return client_id
    return f"source-ip:{_frontend_source_ip(request)}"


def _apply_registered_chat_context(
    payload: ChatRequest,
    request: Request,
) -> ChatRequest:
    context_id = (request.headers.get("x-vision-context-id") or "").strip()
    if not context_id:
        request.state.vision_chat_context = None
        return payload
    if len(context_id) > 128 or not context_id.startswith("ctx_"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="X-Vision-Context-ID 형식이 올바르지 않습니다.",
        )
    try:
        record = chat_context_service.get(
            context_id,
            owner_client_id=_chat_owner_id(request),
        )
    except ChatContextError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    explicit_project = payload.project_id not in {"__auto__", "auto", "default"}
    if explicit_project and record.project_id and payload.project_id != record.project_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Chat Body project_id와 X-Vision-Context-ID의 프로젝트가 다릅니다.",
        )
    if payload.snapshot_id and record.snapshot_id and payload.snapshot_id != record.snapshot_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Chat Body snapshot_id와 X-Vision-Context-ID의 Snapshot이 다릅니다.",
        )
    request.state.vision_chat_context = record
    if not record.grounding_available:
        return payload
    return payload.model_copy(
        update={
            "project_id": record.project_id or payload.project_id,
            "snapshot_id": record.snapshot_id or payload.snapshot_id,
        }
    )


def _has_usable_frontend_context(
    value: str | list[Any],
) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    for item in value:
        item_value = getattr(item, "value", None)
        if isinstance(item_value, str) and item_value.strip():
            return True
        if isinstance(item_value, dict):
            for key in ("content", "text", "selection", "code"):
                candidate = item_value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return True
    return False


def _record_frontend_activity(
    request: Request,
    project_id: str,
    event: str,
) -> None:
    client_type = (request.headers.get("x-client-type") or "").strip().lower()
    client_id = _frontend_client_id(request, project_id)
    if client_type != "vscode-extension":
        return
    if client_id is None:
        return
    try:
        connectivity_store.touch(
            client_id=client_id,
            client_type="vscode-extension",
            project_id=project_id,
            client_version=(request.headers.get("x-client-version") or "").strip()[:100]
            or None,
            event=event,
        )
    except ConnectivityStoreError:
        # Connectivity telemetry must not make ingest/chat fail.
        pass


def _safe_record_api_activity(**values: Any) -> None:
    try:
        connectivity_store.record_api_activity(**values)
    except ConnectivityStoreError:
        # Operational telemetry runs after the response and must never fail it.
        pass


def _safe_record_communication_event(**values: Any) -> None:
    try:
        connectivity_store.record_communication_event(**values)
    except ConnectivityStoreError:
        # Diagnostics are best effort and must never interrupt a user request.
        pass


def _safe_record_frontend_registration_event(**values: Any) -> None:
    try:
        connectivity_store.record_frontend_registration_event(**values)
    except ConnectivityStoreError:
        # Registration auditing must not block Client enrollment or Chat.
        pass


def _safe_record_chat_audit_request(**values: Any) -> None:
    try:
        connectivity_store.record_chat_request(**values)
    except ConnectivityStoreError:
        # Audit logging must not make a user Chat request fail.
        pass


def _safe_complete_chat_audit(**values: Any) -> None:
    try:
        connectivity_store.complete_chat_audit(**values)
    except ConnectivityStoreError:
        # Audit logging must not replace the actual Chat result.
        pass


def _version_check_result(
    project_id: str,
    client: ProjectVersionDescriptor,
    backend_row: dict[str, Any] | None,
) -> ProjectVersionCheckResponse:
    checked_at = datetime.now(timezone.utc)
    if backend_row is None:
        return ProjectVersionCheckResponse(
            project_id=project_id,
            backend_registered=False,
            backend_source="none",
            same_version=None,
            relation="not_found",
            relation_basis="none",
            checked_at=checked_at,
            client=client,
            checks=ProjectVersionChecks(),
            reasons=["Backend DB에 해당 project_id가 등록되어 있지 않습니다."],
        )

    backend_git = None
    if any(
        backend_row.get(key) is not None
        for key in (
            "git_commit_sha",
            "git_branch",
            "git_dirty",
            "git_committed_at",
        )
    ):
        backend_git = GitVersionInfo(
            commit_sha=backend_row.get("git_commit_sha"),
            branch=backend_row.get("git_branch"),
            dirty=backend_row.get("git_dirty"),
            committed_at=backend_row.get("git_committed_at"),
        )
    backend = ProjectVersionDescriptor(
        snapshot_id=backend_row.get("current_snapshot_id"),
        manifest_sha256=backend_row.get("manifest_sha256"),
        structure_sha256=backend_row.get("structure_sha256"),
        entry_count=backend_row.get("entry_count"),
        modified_at=backend_row.get("source_modified_at"),
        git=backend_git,
    )

    def compare(left: Any, right: Any) -> bool | None:
        if left is None or right is None:
            return None
        return left == right

    checks = ProjectVersionChecks(
        snapshot_id=compare(client.snapshot_id, backend.snapshot_id),
        manifest_sha256=compare(client.manifest_sha256, backend.manifest_sha256),
        structure_sha256=compare(
            client.structure_sha256,
            backend.structure_sha256,
        ),
        git_commit_sha=compare(
            client.git.commit_sha if client.git else None,
            backend.git.commit_sha if backend.git else None,
        ),
        git_branch=compare(
            client.git.branch if client.git else None,
            backend.git.branch if backend.git else None,
        ),
        git_dirty=compare(
            client.git.dirty if client.git else None,
            backend.git.dirty if backend.git else None,
        ),
        modified_at=compare(client.modified_at, backend.modified_at),
    )
    strong_version_checks = [
        checks.snapshot_id,
        checks.manifest_sha256,
        checks.git_commit_sha,
    ]
    strong_comparable = [
        value for value in strong_version_checks if value is not None
    ]
    mismatch_checks = [
        checks.snapshot_id,
        checks.manifest_sha256,
        checks.structure_sha256,
        checks.git_commit_sha,
        checks.git_dirty,
    ]
    reasons: list[str] = []
    for label, value in (
        ("snapshot_id", checks.snapshot_id),
        ("manifest_sha256", checks.manifest_sha256),
        ("전체 폴더 구조", checks.structure_sha256),
        ("Git commit", checks.git_commit_sha),
        ("Git dirty 상태", checks.git_dirty),
        ("수정 시각", checks.modified_at),
    ):
        if value is False:
            reasons.append(f"{label}가 Backend 기준 프로젝트와 다릅니다.")

    if any(value is False for value in mismatch_checks if value is not None):
        same_version = False
        client_commit_at = client.git.committed_at if client.git else None
        backend_commit_at = backend.git.committed_at if backend.git else None
        if (
            checks.git_commit_sha is False
            and client_commit_at is not None
            and backend_commit_at is not None
        ):
            relation = (
                "client_newer"
                if client_commit_at > backend_commit_at
                else "backend_newer"
                if client_commit_at < backend_commit_at
                else "diverged"
            )
            relation_basis = "git_committed_at"
        elif (
            checks.structure_sha256 is False
            and client.modified_at is not None
            and backend.modified_at is not None
        ):
            relation = (
                "client_newer"
                if client.modified_at > backend.modified_at
                else "backend_newer"
                if client.modified_at < backend.modified_at
                else "diverged"
            )
            relation_basis = "modified_at"
        else:
            relation = "diverged"
            relation_basis = "version_signals"
    elif strong_comparable and all(strong_comparable):
        same_version: bool | None = True
        relation = "same"
        relation_basis = "exact_match"
        reasons.append("Git commit, manifest 또는 snapshot 버전 정보가 일치합니다.")
    else:
        same_version = None
        relation = "unknown"
        relation_basis = "none"
        if checks.structure_sha256 is True:
            reasons.append(
                "전체 폴더 구조는 일치하지만 파일 내용 버전은 Git commit 또는 "
                "manifest hash 없이 확정할 수 없습니다."
            )
        else:
            reasons.append(
                "양쪽에서 함께 제공한 전체 트리, snapshot, manifest hash 또는 "
                "Git commit이 없습니다."
            )

    return ProjectVersionCheckResponse(
        project_id=project_id,
        backend_registered=True,
        backend_source=backend_row.get("backend_source", "postgresql"),
        same_version=same_version,
        relation=relation,
        relation_basis=relation_basis,
        checked_at=checked_at,
        client=client,
        backend=backend,
        backend_updated_at=backend_row.get("updated_at"),
        checks=checks,
        reasons=reasons,
    )


@app.get("/")
def root() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "vs-code-ai-assistant-backend",
        "api": "/v1",
        "schema_version": API_SCHEMA_VERSION,
        "docs": "/docs",
    }


@app.get("/v1/live", tags=["System"], include_in_schema=False)
def liveness() -> dict[str, str]:
    """Process-only probe. Downstream outages must not trigger a restart loop."""
    return {"status": "ok", "service": "vision-api"}


@app.get("/v1/ready", tags=["System"], include_in_schema=False)
def readiness() -> Response:
    """Replica readiness for orchestrators such as Kubernetes.

    Readiness covers only shared control-plane dependencies required to safely
    accept work: Redis coordination, the Alembic-managed persistence schema, and
    the shared upload workspace. AI/embedding/vector targets are intentionally
    excluded because they are replaceable runtime providers, not Pod lifecycle
    dependencies.
    """
    checks: dict[str, Any] = {}
    ready = True
    try:
        checks["coordination"] = {"ready": redis_coordinator.ping()}
    except DistributedStateError as exc:
        checks["coordination"] = {"ready": False, "error": type(exc).__name__}
        ready = False

    try:
        with psycopg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            dbname=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
            connect_timeout=settings.postgres_connect_timeout_seconds,
        ) as connection:
            inspection = inspect_schema(connection)
        persistence_ready = bool(
            inspection.baseline_compatible and inspection.revision is not None
        )
        checks["persistence"] = {
            "ready": persistence_ready,
            "revision": inspection.revision,
            "expected_revision": CURRENT_REVISION,
        }
        ready = ready and persistence_ready
    except (psycopg.Error, OSError) as exc:
        checks["persistence"] = {"ready": False, "error": type(exc).__name__}
        ready = False

    storage = upload_manager.storage_status()
    storage_ready = bool(storage["exists"] and storage["writable"] and storage["distributed_lock"])
    checks["shared_workspace"] = {**storage, "ready": storage_ready}
    ready = ready and storage_ready

    runtime_setup = runtime_settings_resolver.setup_state(refresh=False)
    checks["runtime_configuration"] = {
        "configured": runtime_setup.configured,
        "missing": list(runtime_setup.missing),
        "errors": list(runtime_setup.errors),
        "lifecycle_blocking": False,
    }

    payload = {"status": "ready" if ready else "not_ready", "checks": checks}
    return JSONResponse(status_code=200 if ready else 503, content=payload)


@app.get("/v1/health", tags=["System"])
def health() -> dict[str, Any]:
    runtime_setup = runtime_settings_resolver.setup_state(refresh=False)
    vector_status: dict[str, Any]
    if runtime_setup.configured:
        try:
            vector_status = vector_store.stats()
        except VectorStoreError as exc:
            vector_status = {"status": "unavailable", "error": type(exc).__name__}
    else:
        vector_status = {
            "status": "setup_required",
            "missing": list(runtime_setup.missing),
        }
    return {
        "status": "ok",
        "service": "vs-code-ai-assistant-backend",
        "version": "3.0.0",
        "instance_id": settings.instance_id,
        "configuration": settings.public_status(),
        "runtime_setup": {
            "configured": runtime_setup.configured,
            "missing": list(runtime_setup.missing),
            "errors": list(runtime_setup.errors),
        },
        "vector_store": vector_status,
        "metadata_store": metadata_store.status(),
        "project_store": project_store.status(),
        "message": "백엔드 API 서버에서 응답중 입니다.",
    }


@app.get(
    "/v1/languages",
    tags=["System"],
    summary="List the VS Code-compatible language registry",
)
def list_languages(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "public, max-age=3600"
    return language_registry().catalog()


@app.post(
    "/v1/languages/detect",
    tags=["System"],
    summary="Detect or normalize one VS Code document language",
)
def detect_language(payload: LanguageDetectRequest) -> dict[str, Any]:
    return language_registry().detect(
        explicit_language_id=payload.language_id,
        file_name=payload.file_name,
        path=payload.path,
        content=payload.content,
        workspace_languages=payload.workspace_languages,
        session_languages=payload.session_languages,
        workspace_history_languages=payload.workspace_history_languages,
        global_history_languages=payload.global_history_languages,
    ).public_dict()

# @app.get("/v1/health")
# def health_check():
#     return {
#         "status": "ok",

#     }


@app.get(
    "/v1/models",
    response_model=ModelListResponse,
    tags=["System"],
    summary="List selectable generation models",
)
def list_models(response: Response) -> ModelListResponse:
    response.headers["Cache-Control"] = "no-store"
    models = [
        model for model in generation_router.models()
        if model.enabled
    ]
    default_model_id = generation_router.default_model_id
    return ModelListResponse(
        catalog_revision=model_catalog_revision(default_model_id, models),
        default_model_id=default_model_id,
        checked_at=datetime.now(timezone.utc),
        models=models,
    )


def _public_index_status(value: Any) -> str:
    normalized = str(value or "not_indexed").strip().lower()
    return {
        "not_indexed": "not_indexed",
        "queued": "queued",
        "indexing": "indexing",
        "ready": "ready",
        "completed": "ready",
        "partially_ready": "partially_ready",
        "partially_completed": "partially_ready",
        "failed": "failed",
        "cancelled": "failed",
        "stale": "stale",
    }.get(normalized, "stale")


@app.get(
    "/v1/IngestResponse",
    response_model=IndexedProjectListResponse,
    tags=["Projects"],
    summary="List backend projects and indexed Git versions",
    description=(
        "Used when the VS Code Extension starts and when the user refreshes "
        "the Sidebar project list."
    ),
    responses=ERROR_RESPONSES,
)
def list_indexed_projects(
    request: Request,
    response: Response,
) -> IndexedProjectListResponse | JSONResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        rows = project_store.list_projects()
    except ProjectStoreError:
        return _error_response(
            request,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "프로젝트 목록을 불러올 수 없습니다.",
            code="PROJECT_REGISTRY_UNAVAILABLE",
            headers={"Cache-Control": "no-store"},
        )

    projects: list[IndexedProjectItem] = []
    for row in rows:
        commit_sha = (row.get("git_commit_sha") or "").strip() or None
        public_status = _public_index_status(row.get("index_status"))
        project_id = row["project_id"]
        project_name = row.get("display_name") or project_id
        if project_name == project_id and "/" in project_id:
            project_name = project_id.rsplit("/", 1)[-1]
        indexed_at = row.get("index_completed_at")
        if indexed_at is None and public_status == "ready":
            # Existing rows created before index_completed_at was introduced
            # retain their last known successful update time.
            indexed_at = row.get("updated_at")
        projects.append(
            IndexedProjectItem(
                project_id=project_id,
                project_name=project_name,
                git_commit_sha=commit_sha,
                git_short_sha=commit_sha[:7] if commit_sha else None,
                git_branch=row.get("git_branch"),
                git_dirty=row.get("git_dirty"),
                git_committed_at=row.get("git_committed_at"),
                active_snapshot_id=row.get("current_snapshot_id"),
                index_status=public_status,
                indexed_at=indexed_at,
            )
        )

    return IndexedProjectListResponse(
        request_id=_request_id(request),
        projects=projects,
        total=len(projects),
        generated_at=datetime.now(timezone.utc),
    )


@app.post("/v1/client-heartbeat", response_model=ClientHeartbeatResponse, tags=["System"])
def client_heartbeat(
    payload: ClientHeartbeatRequest,
    request: Request,
) -> ClientHeartbeatResponse:
    header_client_id = (request.headers.get("x-client-id") or "").strip()
    if header_client_id and header_client_id != payload.client_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="X-Client-ID does not match heartbeat client_id",
        )
    try:
        row = connectivity_store.touch(
            client_id=payload.client_id,
            client_type="vscode-extension",
            project_id=payload.project_id,
            client_version=payload.client_version,
            event="heartbeat",
            details=payload.details,
        )
    except ConnectivityStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL connectivity store is unavailable",
        ) from exc
    return ClientHeartbeatResponse(
        client_id=row["client_id"],
        project_id=row["project_id"],
        last_seen_at=row["last_seen_at"],
    )


@app.get(
    "/v1/admin/persistence-status",
    response_model=dict[str, Any],
    tags=["System"],
    include_in_schema=False,
)
def persistence_status(request: Request) -> dict[str, Any]:
    """Expose database capabilities by role without making the engine the UI concept."""
    _require_admin_proxy(request)
    checked_at = datetime.now(timezone.utc)
    unavailable_capabilities = [
        {
            "id": group["id"],
            "role": group["role"],
            "description": group["description"],
            "status": "unavailable",
            "tables": list(group["tables"]),
            "table_count": len(group["tables"]),
            "records_estimate": None,
        }
        for group in PERSISTENCE_CAPABILITY_GROUPS
    ]
    if not settings.postgres_password:
        return {
            "checked_at": checked_at.isoformat(),
            "status": "unavailable",
            "implementation": {"engine": "postgresql", "schema": "public"},
            "schema": {
                "managed": False,
                "revision": None,
                "expected_revision": CURRENT_REVISION,
                "baseline_compatible": False,
                "missing_tables": list(BASELINE_TABLE_COLUMNS),
                "missing_columns": [],
            },
            "capabilities": unavailable_capabilities,
            "error": "persistence credentials are not configured",
        }

    try:
        with psycopg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            dbname=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
            connect_timeout=settings.postgres_connect_timeout_seconds,
            row_factory=dict_row,
        ) as connection:
            inspection = inspect_schema(connection)
            stats_rows = connection.execute(
                """
                SELECT relname AS table_name, COALESCE(n_live_tup, 0)::BIGINT AS rows_estimate
                FROM pg_stat_user_tables
                WHERE schemaname = 'public'
                """
            ).fetchall()
    except (psycopg.Error, OSError) as exc:
        return {
            "checked_at": checked_at.isoformat(),
            "status": "unavailable",
            "implementation": {"engine": "postgresql", "schema": "public"},
            "schema": {
                "managed": False,
                "revision": None,
                "expected_revision": CURRENT_REVISION,
                "baseline_compatible": False,
                "missing_tables": [],
                "missing_columns": [],
            },
            "capabilities": unavailable_capabilities,
            "error": type(exc).__name__,
        }

    row_estimates = {
        str(row["table_name"]): int(row["rows_estimate"] or 0)
        for row in stats_rows
    }
    missing_tables = set(inspection.missing_tables)
    missing_columns = tuple(inspection.missing_columns)
    capabilities: list[dict[str, Any]] = []
    for group in PERSISTENCE_CAPABILITY_GROUPS:
        tables = tuple(group["tables"])
        group_missing_tables = sorted(table for table in tables if table in missing_tables)
        group_missing_columns = sorted(
            column
            for column in missing_columns
            if column.split(".", 1)[0] in tables
        )
        ready = not group_missing_tables and not group_missing_columns
        capabilities.append(
            {
                "id": group["id"],
                "role": group["role"],
                "description": group["description"],
                "status": "ready" if ready else "degraded",
                "tables": list(tables),
                "table_count": len(tables),
                "records_estimate": (
                    sum(row_estimates.get(table, 0) for table in tables) if ready else None
                ),
                "missing_tables": group_missing_tables,
                "missing_columns": group_missing_columns,
            }
        )

    managed = inspection.revision is not None
    if not inspection.baseline_compatible:
        overall = "degraded"
    elif inspection.revision is None:
        overall = "migration_required"
    elif inspection.revision == CURRENT_REVISION:
        overall = "ready"
    else:
        overall = "revision_mismatch"
    return {
        "checked_at": checked_at.isoformat(),
        "status": overall,
        "implementation": {"engine": "postgresql", "schema": "public"},
        "schema": {
            "managed": managed,
            "revision": inspection.revision,
            "expected_revision": CURRENT_REVISION,
            "baseline_compatible": inspection.baseline_compatible,
            "missing_tables": list(inspection.missing_tables),
            "missing_columns": list(inspection.missing_columns),
        },
        "capabilities": capabilities,
        "error": None,
    }


@app.get(
    "/v1/admin/connectivity",
    response_model=ConnectivityStatusResponse,
    tags=["System"],
)
def connectivity_status(request: Request) -> ConnectivityStatusResponse:
    _require_admin_proxy(request)
    checked_at = datetime.now(timezone.utc)
    try:
        latest_frontend = connectivity_store.latest("vscode-extension")
    except ConnectivityStoreError:
        latest_frontend = None

    if latest_frontend is None:
        frontend = FrontendConnectivityStatus(
            status="unknown",
            connected=False,
        )
    else:
        last_seen_at = latest_frontend["last_seen_at"]
        age_seconds = max(0, round((checked_at - last_seen_at).total_seconds()))
        if age_seconds <= 75:
            frontend_state = "online"
        elif age_seconds <= 180:
            frontend_state = "stale"
        else:
            frontend_state = "offline"
        frontend = FrontendConnectivityStatus(
            status=frontend_state,
            connected=frontend_state == "online",
            client_id=latest_frontend["client_id"],
            project_id=latest_frontend["project_id"],
            client_version=latest_frontend["client_version"],
            last_event=latest_frontend["last_event"],
            last_seen_at=last_seen_at,
            age_seconds=age_seconds,
        )

    backendai_probe = generation_router.backendai_status()
    backendai = BackendAIConnectivityStatus(
        **backendai_probe,
        model_id=settings.backendai_public_model_id,
        model=settings.backendai_model,
    )
    return ConnectivityStatusResponse(
        checked_at=checked_at,
        frontend=frontend,
        backendai=backendai,
    )


@app.get(
    "/v1/admin/project-snapshots/status",
    response_model=dict[str, Any],
    tags=["System"],
    include_in_schema=False,
)
def snapshot_admin_status(request: Request) -> dict[str, Any]:
    _require_admin_proxy(request)
    return {
        "tenant_id": settings.snapshot_tenant_id,
        "github_authentication": (
            "authenticated" if snapshot_service.github_authenticated else "anonymous"
        ),
        "github_token_configured": bool(settings.snapshot_github_token),
        "allowed_repositories": sorted(settings.snapshot_allowed_repositories),
        "token_exposed": False,
    }


@app.post(
    "/v1/admin/project-snapshots/import",
    response_model=SnapshotImportResponse,
    tags=["System"],
    include_in_schema=False,
)
def import_snapshot(
    payload: SnapshotImportRequest,
    request: Request,
) -> SnapshotImportResponse:
    _require_admin_proxy(request)
    try:
        return snapshot_service.import_github_snapshot(
            payload.repository_url,
            payload.ref,
        )
    except SnapshotServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=str(exc),
        ) from exc


@app.get(
    "/v1/admin/project-snapshots",
    response_model=dict[str, Any],
    tags=["System"],
    include_in_schema=False,
)
def list_snapshots(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    _require_admin_proxy(request)
    try:
        snapshots = snapshot_repository.list_snapshots(limit=limit)
        repositories = snapshot_repository.list_repositories(limit=limit)
    except SnapshotRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return {
        "snapshots": [item.model_dump(mode="json") for item in snapshots],
        "repositories": [item.model_dump(mode="json") for item in repositories],
        "total_snapshots": len(snapshots),
        "total_repositories": len(repositories),
    }


@app.get(
    "/v1/admin/project-snapshots/{snapshot_id}",
    response_model=dict[str, Any],
    tags=["System"],
    include_in_schema=False,
)
def get_snapshot(snapshot_id: str, request: Request) -> dict[str, Any]:
    _require_admin_proxy(request)
    try:
        snapshot = snapshot_repository.get_snapshot(snapshot_id)
    except SnapshotRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Snapshot was not found")
    return snapshot.model_dump(mode="json")


@app.get(
    "/v1/admin/runtime-metrics",
    response_model=dict[str, Any],
    tags=["System"],
    include_in_schema=False,
)
def runtime_metrics(request: Request) -> dict[str, Any]:
    _require_admin_proxy(request)
    try:
        return redis_coordinator.snapshot()
    except DistributedStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis runtime metrics are unavailable",
        ) from exc


@app.get(
    "/v1/admin/api-activity",
    response_model=APIEndpointActivityResponse,
    tags=["System"],
    include_in_schema=False,
)
def api_activity_status(request: Request) -> APIEndpointActivityResponse:
    _require_admin_proxy(request)
    try:
        rows = connectivity_store.latest_api_activity()
    except ConnectivityStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL API activity store is unavailable",
        ) from exc
    by_endpoint = {
        (row["method"], row["path"]): row
        for row in rows
    }
    endpoints: list[APIEndpointActivity] = []
    for method, path in MONITORED_FRONTEND_ENDPOINTS:
        row = by_endpoint.get((method, path))
        if row is None:
            endpoints.append(
                APIEndpointActivity(
                    method=method,
                    path=path,
                    requested=False,
                    responded=False,
                    success=False,
                )
            )
            continue
        status_code = row["last_status_code"]
        endpoints.append(
            APIEndpointActivity(
                method=method,
                path=path,
                requested=row["last_request_at"] is not None,
                responded=row["last_response_at"] is not None,
                success=(
                    status_code is not None and 200 <= int(status_code) < 300
                ),
                last_status_code=status_code,
                last_request_at=row["last_request_at"],
                last_response_at=row["last_response_at"],
                last_success_at=row["last_success_at"],
                last_duration_ms=row["last_duration_ms"],
                last_request_id=row["last_request_id"],
                client_id=row["client_id"],
                request_count=int(row["request_count"]),
                success_count=int(row["success_count"]),
                error_count=int(row["error_count"]),
            )
        )
    return APIEndpointActivityResponse(
        checked_at=datetime.now(timezone.utc),
        endpoints=endpoints,
    )


@app.get(
    "/v1/admin/communication-logs",
    response_model=CommunicationEventListResponse,
    tags=["System"],
    include_in_schema=False,
)
def communication_logs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> CommunicationEventListResponse:
    _require_admin_proxy(request)
    try:
        rows = connectivity_store.latest_communication_events(limit=limit)
    except ConnectivityStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL communication log is unavailable",
        ) from exc
    return CommunicationEventListResponse(
        checked_at=datetime.now(timezone.utc),
        events=[CommunicationEvent(**row) for row in rows],
    )


@app.get(
    "/v1/admin/chat-audit-logs",
    response_model=ChatAuditLogListResponse,
    tags=["System"],
    include_in_schema=False,
)
def chat_audit_logs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> ChatAuditLogListResponse:
    _require_admin_proxy(request)
    try:
        rows = connectivity_store.latest_chat_audit_logs(limit=limit)
    except ConnectivityStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL Chat audit log is unavailable",
        ) from exc
    return ChatAuditLogListResponse(
        checked_at=datetime.now(timezone.utc),
        logs=[ChatAuditLog(**row) for row in rows],
    )


@app.get(
    "/v1/admin/chat-sessions",
    response_model=ChatSessionListResponse,
    tags=["System"],
    include_in_schema=False,
)
def chat_sessions(
    request: Request,
    limit: int = Query(default=500, ge=1, le=1_000),
) -> ChatSessionListResponse:
    """Return the audit-backed user/session hierarchy for the Playground."""

    _require_admin_proxy(request)
    try:
        users = connectivity_store.latest_chat_sessions(limit=limit)
    except ConnectivityStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL Chat session history is unavailable",
        ) from exc
    typed_users = [ChatSessionUser(**user) for user in users]
    return ChatSessionListResponse(
        checked_at=datetime.now(timezone.utc),
        users=typed_users,
        total_users=len(typed_users),
        total_sessions=sum(len(user.sessions) for user in typed_users),
    )


@app.get(
    "/v1/admin/chat-session",
    response_model=ChatSessionSummary,
    tags=["System"],
    include_in_schema=False,
)
def chat_session(
    request: Request,
    client_id: str = Query(..., min_length=1, max_length=255),
    session_id: str = Query(..., min_length=1, max_length=255),
    limit: int = Query(default=200, ge=1, le=200),
) -> ChatSessionSummary:
    _require_admin_proxy(request)
    try:
        result = connectivity_store.chat_session(
            client_id=client_id,
            session_id=session_id,
            limit=limit,
        )
    except ConnectivityStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL Chat session messages are unavailable",
        ) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return ChatSessionSummary(**result)


@app.get(
    "/v1/admin/frontend-registration-logs",
    response_model=FrontendRegistrationEventListResponse,
    tags=["System"],
    include_in_schema=False,
)
def frontend_registration_logs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> FrontendRegistrationEventListResponse:
    _require_admin_proxy(request)
    try:
        rows = connectivity_store.latest_frontend_registration_events(
            limit=limit
        )
    except ConnectivityStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL Frontend registration log is unavailable",
        ) from exc
    return FrontendRegistrationEventListResponse(
        checked_at=datetime.now(timezone.utc),
        events=[FrontendRegistrationEvent(**row) for row in rows],
    )


@app.post(
    "/v1/admin/ai-probe",
    response_model=AICommunicationProbeResponse,
    tags=["System"],
    include_in_schema=False,
)
def ai_communication_probe(
    payload: AICommunicationProbeRequest,
    request: Request,
) -> AICommunicationProbeResponse:
    """Run a real model chat probe without storing prompt or answer bodies."""

    _require_admin_proxy(request)
    request_id = _request_id(request)
    requested_model_id = payload.model_id or generation_router.default_model_id
    started_at = perf_counter()
    _safe_record_communication_event(
        request_id=request_id,
        channel="fastapi-ai",
        direction="fastapi_to_ai_server",
        phase="ai.request",
        status="started",
        provider="model-router",
        model=requested_model_id,
        details={"probe": True},
    )
    try:
        generation = generation_router.generate(
            requested_model_id,
            "Reply with exactly VISION_AI_OK.",
            [],
            [],
            "",
            "system-diagnostic",
            "system-diagnostic",
            request_id=request_id,
        )
    except ServiceError as exc:
        latency_ms = round((perf_counter() - started_at) * 1000)
        _safe_record_communication_event(
            request_id=request_id,
            channel="fastapi-ai",
            direction="ai_server_to_fastapi",
            phase="ai.response",
            status="error",
            status_code=getattr(exc, "status_code", 503),
            duration_ms=latency_ms,
            provider="model-router",
            model=requested_model_id,
            error=str(exc),
            details={"probe": True},
        )
        raise _service_error(exc) from exc

    latency_ms = round((perf_counter() - started_at) * 1000)
    answer_preview = generation.answer.strip().replace("\r", " ").replace("\n", " ")[:160]
    probe_status = (
        "ok"
        if "VISION_AI_OK" in generation.answer
        else "unexpected_answer"
    )
    _safe_record_communication_event(
        request_id=request_id,
        channel="fastapi-ai",
        direction="ai_server_to_fastapi",
        phase="ai.response",
        status="success" if probe_status == "ok" else "warning",
        status_code=200,
        duration_ms=latency_ms,
        provider=generation.provider,
        model=generation.used_model_name,
        details={
            "probe": True,
            "expected_marker_received": probe_status == "ok",
            "answer_chars": len(generation.answer),
        },
    )
    return AICommunicationProbeResponse(
        status=probe_status,
        request_id=request_id,
        checked_at=datetime.now(timezone.utc),
        requested_model_id=generation.requested_model_id,
        used_model_id=generation.used_model_id,
        provider=generation.provider,
        model=generation.used_model_name,
        latency_ms=latency_ms,
        answer_preview=answer_preview,
    )


@app.get(
    "/v1/admin/frontend-clients",
    response_model=FrontendClientListResponse,
    tags=["System"],
    include_in_schema=False,
)
def list_frontend_clients(request: Request) -> FrontendClientListResponse:
    _require_admin_proxy(request)
    try:
        rows = frontend_client_store.list_with_probes()
    except FrontendClientStoreError as exc:
        raise _frontend_client_store_error(exc) from exc
    records = [
        _frontend_client_record(client, probe)
        for client, probe in rows
    ]
    return FrontendClientListResponse(
        clients=records,
        total=len(records),
        enabled=sum(1 for item in records if item.enabled),
        reachable=sum(1 for item in records if item.reachable),
    )


@app.post(
    "/v1/admin/frontend-clients",
    response_model=FrontendClientRecord,
    status_code=status.HTTP_201_CREATED,
    tags=["System"],
    include_in_schema=False,
)
def create_frontend_client(
    payload: FrontendClientWriteRequest,
    request: Request,
) -> FrontendClientRecord:
    _require_admin_proxy(request)
    try:
        client = frontend_client_store.create(
            name=payload.name,
            ip=payload.ip,
            port=payload.port,
            enabled=payload.enabled,
            chat_deep_normalization_mode=payload.chat_deep_normalization_mode,
        )
    except FrontendClientStoreError as exc:
        raise _frontend_client_store_error(exc) from exc
    return _frontend_client_record(client)


@app.put(
    "/v1/admin/frontend-clients/{client_id}",
    response_model=FrontendClientRecord,
    tags=["System"],
    include_in_schema=False,
)
def update_frontend_client(
    client_id: str,
    payload: FrontendClientWriteRequest,
    request: Request,
) -> FrontendClientRecord:
    _require_admin_proxy(request)
    try:
        client = frontend_client_store.update(
            client_id,
            name=payload.name,
            ip=payload.ip,
            port=payload.port,
            enabled=payload.enabled,
            chat_deep_normalization_mode=payload.chat_deep_normalization_mode,
        )
    except FrontendClientStoreError as exc:
        raise _frontend_client_store_error(exc) from exc
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Frontend client was not found",
        )
    return _frontend_client_record(client)


@app.delete(
    "/v1/admin/frontend-clients/{client_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["System"],
    include_in_schema=False,
)
def delete_frontend_client(client_id: str, request: Request) -> None:
    _require_admin_proxy(request)
    try:
        deleted = frontend_client_store.delete(client_id)
    except FrontendClientStoreError as exc:
        raise _frontend_client_store_error(exc) from exc
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Frontend client was not found",
        )


@app.get(
    "/v1/admin/chat-intake-settings",
    response_model=ChatIntakeSettingsResponse,
    tags=["System"],
    include_in_schema=False,
)
def get_chat_intake_settings(request: Request) -> ChatIntakeSettingsResponse:
    _require_admin_proxy(request)
    try:
        value = chat_intake_settings_store.get()
    except ChatIntakeSettingsError as exc:
        raise _chat_intake_settings_error(exc) from exc
    return ChatIntakeSettingsResponse(
        deep_normalization_enabled=value.deep_normalization_enabled,
        fallback_mode=value.fallback_mode,
        updated_at=value.updated_at,
    )


@app.put(
    "/v1/admin/chat-intake-settings",
    response_model=ChatIntakeSettingsResponse,
    tags=["System"],
    include_in_schema=False,
)
def update_chat_intake_settings(
    payload: ChatIntakeSettingsUpdateRequest,
    request: Request,
) -> ChatIntakeSettingsResponse:
    _require_admin_proxy(request)
    try:
        value = chat_intake_settings_store.update(
            deep_normalization_enabled=payload.deep_normalization_enabled,
            fallback_mode=payload.fallback_mode,
        )
    except ChatIntakeSettingsError as exc:
        raise _chat_intake_settings_error(exc) from exc
    return ChatIntakeSettingsResponse(
        deep_normalization_enabled=value.deep_normalization_enabled,
        fallback_mode=value.fallback_mode,
        updated_at=value.updated_at,
    )


@app.get(
    "/v1/admin/setup-status",
    response_model=RuntimeSetupStatusResponse,
    tags=["System"],
    include_in_schema=False,
)
def get_runtime_setup_status(request: Request) -> RuntimeSetupStatusResponse:
    _require_admin_proxy(request)
    state = runtime_settings_resolver.setup_state(refresh=True)
    return RuntimeSetupStatusResponse(
        status="configured" if state.configured else "setup_required",
        configured=state.configured,
        missing=list(state.missing),
        errors=list(state.errors),
        service_settings_configured=state.service_settings_configured,
        network_settings_configured=state.network_settings_configured,
        vector_target_configured=state.vector_target_configured,
        embedding_profile_configured=state.embedding_profile_configured,
    )


@app.get(
    "/v1/admin/network-settings",
    response_model=NetworkSettingsResponse,
    tags=["System"],
    include_in_schema=False,
)
def get_network_settings(request: Request) -> NetworkSettingsResponse:
    _require_admin_proxy(request)
    try:
        value = runtime_network_store.get(refresh=True)
    except RuntimeNetworkSettingsError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL runtime network settings are unavailable",
        ) from exc
    return _network_settings_response(value)


@app.put(
    "/v1/admin/network-settings",
    response_model=NetworkSettingsResponse,
    tags=["System"],
    include_in_schema=False,
)
def update_network_settings(
    payload: NetworkSettingsUpdateRequest,
    request: Request,
) -> NetworkSettingsResponse:
    _require_admin_proxy(request)
    try:
        value = runtime_network_store.update(
            frontend_ip=payload.frontend.ip,
            frontend_port=payload.frontend.port,
            backendai_ip=payload.backendai.ip,
            backendai_port=payload.backendai.port,
        )
    except RuntimeNetworkSettingsError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL runtime network settings are unavailable",
        ) from exc
    runtime_settings_resolver.invalidate()
    generation_router.invalidate_backendai_status()
    return _network_settings_response(value)


def _vector_target_response(
    target: VectorTargetRecord,
    *,
    active_vector_target_id: str | None = None,
) -> VectorTargetRecordResponse:
    return VectorTargetRecordResponse(
        vector_target_id=target.vector_target_id,
        tenant_id=target.tenant_id,
        name=target.name,
        engine=target.engine,
        endpoint=target.endpoint,
        credential_ref=target.credential_ref,
        deployment_type=target.deployment_type,
        capabilities=target.capabilities,
        status=target.status,
        error=target.error,
        latency_ms=target.latency_ms,
        last_checked_at=target.last_checked_at,
        active=target.vector_target_id == active_vector_target_id,
        created_at=target.created_at,
        updated_at=target.updated_at,
    )


def _active_vector_target_id() -> str | None:
    try:
        runtime = runtime_service_store.get(refresh=True)
    except RuntimeServiceSettingsError:
        runtime = runtime_service_store.cached()
    if runtime is None or not runtime.vector.vector_target_id:
        return None
    return runtime.vector.vector_target_id


@app.get(
    "/v1/admin/vector-targets",
    response_model=VectorTargetListResponse,
    tags=["System"],
    include_in_schema=False,
)
def list_vector_targets(request: Request) -> VectorTargetListResponse:
    _require_admin_proxy(request)
    try:
        targets = vector_target_store.list()
    except VectorTargetStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL VectorTarget registry is unavailable",
        ) from exc
    active_id = _active_vector_target_id()
    return VectorTargetListResponse(
        targets=[_vector_target_response(target, active_vector_target_id=active_id) for target in targets],
        active_vector_target_id=active_id,
    )


@app.post(
    "/v1/admin/vector-targets",
    response_model=VectorTargetRecordResponse,
    tags=["System"],
    include_in_schema=False,
)
def create_vector_target(
    payload: VectorTargetWriteRequest,
    request: Request,
) -> VectorTargetRecordResponse:
    _require_admin_proxy(request)
    try:
        target = vector_target_store.upsert_qdrant(
            endpoint=payload.endpoint,
            name=payload.name,
            credential_ref=payload.credential_ref,
        )
    except (VectorTargetStoreError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return _vector_target_response(target, active_vector_target_id=_active_vector_target_id())


@app.post(
    "/v1/admin/vector-targets/{vector_target_id}/verify",
    response_model=VectorTargetRecordResponse,
    tags=["System"],
    include_in_schema=False,
)
def verify_vector_target(
    vector_target_id: str,
    request: Request,
) -> VectorTargetRecordResponse:
    _require_admin_proxy(request)
    try:
        target = vector_target_store.get(vector_target_id)
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VectorTarget not found")
        health = QdrantVectorAdapter(
            target.endpoint,
            bootstrap_settings.qdrant_api_key,
            bootstrap_settings.request_timeout_seconds,
        ).health()
        target = vector_target_store.set_status(
            vector_target_id,
            status="healthy" if health.reachable else "unavailable",
            error=health.detail,
            latency_ms=(round(health.latency_ms) if health.latency_ms is not None else None),
            checked=True,
        )
    except VectorTargetStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VectorTarget verification failed",
        ) from exc
    runtime_settings_resolver.invalidate()
    return _vector_target_response(target, active_vector_target_id=_active_vector_target_id())


@app.post(
    "/v1/admin/vector-targets/{vector_target_id}/select",
    response_model=RuntimeServiceSettingsResponse,
    tags=["System"],
    include_in_schema=False,
)
def select_vector_target(
    vector_target_id: str,
    request: Request,
) -> RuntimeServiceSettingsResponse:
    _require_admin_proxy(request)
    try:
        target = vector_target_store.get(vector_target_id)
        if (
            target is None
            or not target.enabled
            or target.tenant_id != (bootstrap_settings.snapshot_tenant_id.strip() or "vision-default")
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Enabled VectorTarget not found")
        value = runtime_service_store.select_vector_target(vector_target_id)
    except VectorTargetStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VectorTarget registry is unavailable",
        ) from exc
    except RuntimeServiceSettingsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    runtime_settings_resolver.invalidate()
    return _runtime_service_settings_response(value)


def _embedding_profile_response(
    profile: EmbeddingProfileRecord,
    *,
    active_embedding_profile_id: str | None = None,
) -> EmbeddingProfileRecordResponse:
    return EmbeddingProfileRecordResponse(
        embedding_profile_id=profile.embedding_profile_id,
        tenant_id=profile.tenant_id,
        name=profile.name,
        deployment=profile.deployment,
        provider=profile.provider,
        base_url=profile.base_url,
        model=profile.model,
        model_id=profile.model_id,
        dimension=profile.dimension,
        batch_size=profile.batch_size,
        credential_ref=profile.credential_ref,
        status=profile.status,
        error=profile.error,
        latency_ms=profile.latency_ms,
        last_checked_at=profile.last_checked_at,
        active=profile.embedding_profile_id == active_embedding_profile_id,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _active_embedding_profile_id() -> str | None:
    try:
        runtime = runtime_service_store.get(refresh=True)
    except RuntimeServiceSettingsError:
        runtime = runtime_service_store.cached()
    if runtime is None or not runtime.vector.embedding_profile_id:
        return None
    return runtime.vector.embedding_profile_id


@app.get(
    "/v1/admin/embedding-profiles",
    response_model=EmbeddingProfileListResponse,
    tags=["System"],
    include_in_schema=False,
)
def list_embedding_profiles(request: Request) -> EmbeddingProfileListResponse:
    _require_admin_proxy(request)
    try:
        profiles = embedding_profile_store.list()
    except EmbeddingProfileStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL EmbeddingProfile registry is unavailable",
        ) from exc
    active_id = _active_embedding_profile_id()
    return EmbeddingProfileListResponse(
        profiles=[
            _embedding_profile_response(profile, active_embedding_profile_id=active_id)
            for profile in profiles
        ],
        active_embedding_profile_id=active_id,
    )


@app.post(
    "/v1/admin/embedding-profiles",
    response_model=EmbeddingProfileRecordResponse,
    tags=["System"],
    include_in_schema=False,
)
def create_embedding_profile(
    payload: EmbeddingProfileWriteRequest,
    request: Request,
) -> EmbeddingProfileRecordResponse:
    _require_admin_proxy(request)
    try:
        profile = embedding_profile_store.upsert(
            name=payload.name,
            deployment=payload.deployment,
            provider=payload.provider,
            base_url=payload.base_url,
            model=payload.model,
            model_id=payload.model_id,
            dimension=payload.dimension,
            batch_size=payload.batch_size,
            credential_ref=payload.credential_ref,
        )
    except (EmbeddingProfileStoreError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return _embedding_profile_response(
        profile, active_embedding_profile_id=_active_embedding_profile_id()
    )


@app.post(
    "/v1/admin/embedding-profiles/{embedding_profile_id}/verify",
    response_model=EmbeddingProfileRecordResponse,
    tags=["System"],
    include_in_schema=False,
)
def verify_embedding_profile(
    embedding_profile_id: str,
    request: Request,
) -> EmbeddingProfileRecordResponse:
    _require_admin_proxy(request)
    try:
        profile = embedding_profile_store.get(embedding_profile_id)
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="EmbeddingProfile not found"
            )
        probe_settings = replace(
            bootstrap_settings,
            embedding_profile_id=profile.embedding_profile_id,
            embedding_deployment=profile.deployment,
            embedding_provider=profile.provider,
            embedding_base_url=profile.base_url,
            embedding_model=profile.model,
            embedding_model_id=profile.model_id,
            embedding_dimension=profile.dimension,
            embedding_batch_size=profile.batch_size,
        )
        started = perf_counter()
        try:
            EmbeddingService(probe_settings).embed("vision embedding profile verification", "query")
            latency_ms = round((perf_counter() - started) * 1000)
            profile = embedding_profile_store.set_status(
                embedding_profile_id,
                status="healthy",
                error=None,
                latency_ms=latency_ms,
                checked=True,
            )
        except ServiceError as exc:
            latency_ms = round((perf_counter() - started) * 1000)
            profile = embedding_profile_store.set_status(
                embedding_profile_id,
                status="unavailable",
                error=str(exc),
                latency_ms=latency_ms,
                checked=True,
            )
    except EmbeddingProfileStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="EmbeddingProfile verification failed",
        ) from exc
    runtime_settings_resolver.invalidate()
    return _embedding_profile_response(
        profile, active_embedding_profile_id=_active_embedding_profile_id()
    )


@app.post(
    "/v1/admin/embedding-profiles/{embedding_profile_id}/select",
    response_model=RuntimeServiceSettingsResponse,
    tags=["System"],
    include_in_schema=False,
)
def select_embedding_profile(
    embedding_profile_id: str,
    request: Request,
) -> RuntimeServiceSettingsResponse:
    _require_admin_proxy(request)
    try:
        profile = embedding_profile_store.get(embedding_profile_id)
        if (
            profile is None
            or not profile.enabled
            or profile.tenant_id != (bootstrap_settings.snapshot_tenant_id.strip() or "vision-default")
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Enabled EmbeddingProfile not found",
            )
        value = runtime_service_store.select_embedding_profile(embedding_profile_id)
    except EmbeddingProfileStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="EmbeddingProfile registry is unavailable",
        ) from exc
    except RuntimeServiceSettingsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    runtime_settings_resolver.invalidate()
    return _runtime_service_settings_response(value)


@app.get(
    "/v1/admin/service-settings",
    response_model=RuntimeServiceSettingsResponse,
    tags=["System"],
    include_in_schema=False,
)
def get_runtime_service_settings(
    request: Request,
) -> RuntimeServiceSettingsResponse:
    _require_admin_proxy(request)
    try:
        value = runtime_service_store.get(refresh=True)
    except RuntimeServiceSettingsError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL runtime service settings are unavailable",
        ) from exc
    return _runtime_service_settings_response(value)


def _vector_index_response(record: VectorIndexRecord) -> VectorIndexRecordResponse:
    return VectorIndexRecordResponse(
        vector_index_id=record.vector_index_id,
        tenant_id=record.tenant_id,
        name=record.name,
        vector_target_id=record.vector_target_id,
        embedding_profile_id=record.embedding_profile_id,
        collection=record.collection,
        selector=record.selector,
        index_version=record.index_version,
        distance_metric=record.distance_metric,
        ownership_mode=record.ownership_mode,
        query_strategy=record.query_strategy,
        status=record.status,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@app.get(
    "/v1/admin/vector-indexes",
    response_model=VectorIndexListResponse,
    tags=["System"],
    include_in_schema=False,
)
def list_vector_indexes(request: Request) -> VectorIndexListResponse:
    _require_admin_proxy(request)
    try:
        records = vector_index_store.list(
            tenant_id=bootstrap_settings.snapshot_tenant_id.strip() or "vision-default"
        )
    except VectorIndexStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL VectorIndex registry is unavailable",
        ) from exc
    return VectorIndexListResponse(
        indexes=[_vector_index_response(record) for record in records],
        total=len(records),
    )


@app.get(
    "/v1/admin/vector-indexes/{vector_index_id}",
    response_model=VectorIndexRecordResponse,
    tags=["System"],
    include_in_schema=False,
)
def get_vector_index(
    vector_index_id: str, request: Request
) -> VectorIndexRecordResponse:
    _require_admin_proxy(request)
    try:
        record = vector_index_store.get(vector_index_id)
    except VectorIndexStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL VectorIndex registry is unavailable",
        ) from exc
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VectorIndex not found")
    return _vector_index_response(record)


def _external_vector_index_verification_response(
    record: ExternalVectorIndexVerificationRecord,
) -> ExternalVectorIndexVerificationResponse:
    return ExternalVectorIndexVerificationResponse(
        vector_index_id=record.vector_index_id,
        tenant_id=record.tenant_id,
        verification_state=record.verification_state,
        verification_method=record.verification_method,
        embedding_profile_attested=record.embedding_profile_attested,
        expected_dimension=record.expected_dimension,
        observed_dimension=record.observed_dimension,
        expected_distance_metric=record.expected_distance_metric,
        observed_distance_metric=record.observed_distance_metric,
        observed_vector_type=record.observed_vector_type,
        observed_points_count=record.observed_points_count,
        selector_points_count=record.selector_points_count,
        sample_size=record.sample_size,
        sample_payload_keys=record.sample_payload_keys,
        last_verified_at=record.last_verified_at,
        error=record.error,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _external_adapter(target: VectorTargetRecord) -> QdrantVectorAdapter:
    if target.engine != "qdrant":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"External VectorIndex discovery is unsupported for engine={target.engine}",
        )
    if not target.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="VectorTarget is disabled",
        )
    return QdrantVectorAdapter(
        target.endpoint,
        bootstrap_settings.qdrant_api_key,
        bootstrap_settings.request_timeout_seconds,
    )


@app.get(
    "/v1/admin/vector-targets/{vector_target_id}/indexes/discover",
    response_model=ExternalVectorIndexDiscoveryResponse,
    tags=["System"],
    include_in_schema=False,
)
def discover_external_vector_indexes(
    vector_target_id: str,
    request: Request,
) -> ExternalVectorIndexDiscoveryResponse:
    """Discover physical collections without attaching or trusting them."""
    _require_admin_proxy(request)
    try:
        target = vector_target_store.get(vector_target_id)
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VectorTarget not found")
        states = _external_adapter(target).discover_indexes()
    except VectorTargetStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VectorTarget registry is unavailable",
        ) from exc
    except VectorStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"External VectorIndex discovery failed: {exc}",
        ) from exc
    items = [
        ExternalVectorIndexDiscoveryItem(
            collection=item.collection,
            dimension=item.dimension,
            distance_metric=item.distance_metric,
            vector_type=item.vector_type,
            points_count=item.points_count,
            status=item.status,
        )
        for item in states
    ]
    return ExternalVectorIndexDiscoveryResponse(
        vector_target_id=vector_target_id,
        indexes=items,
        total=len(items),
    )


@app.post(
    "/v1/admin/vector-indexes/attach",
    response_model=ExternalVectorIndexAttachResponse,
    tags=["System"],
    include_in_schema=False,
)
def attach_external_vector_index(
    payload: ExternalVectorIndexAttachRequest,
    request: Request,
) -> ExternalVectorIndexAttachResponse:
    """Attach an existing collection as unverified external logical data."""
    _require_admin_proxy(request)
    try:
        target = vector_target_store.get(payload.vector_target_id)
        profile = embedding_profile_store.get(payload.embedding_profile_id)
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VectorTarget not found")
        if profile is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="EmbeddingProfile not found")
        if not profile.enabled:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="EmbeddingProfile is disabled")
        adapter = _external_adapter(target)
        state = adapter.describe_index(VectorIndexRef(collection=payload.collection))
        if not state.exists:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"External collection does not exist: {payload.collection}",
            )
        distance_metric = payload.distance_metric or state.distance_metric
        if not distance_metric:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="External collection distance metric cannot be determined; specify distance_metric explicitly",
            )
        index = vector_index_store.register_external(
            tenant_id=bootstrap_settings.snapshot_tenant_id.strip() or "vision-default",
            name=payload.name or payload.collection,
            vector_target_id=target.vector_target_id,
            embedding_profile_id=profile.embedding_profile_id,
            collection=payload.collection,
            selector=payload.selector,
            index_version=payload.index_version,
            distance_metric=distance_metric,
            query_strategy=payload.query_strategy,
        )
        verification = external_vector_index_verification_store.get(index.vector_index_id)
        if verification is None:
            verification = external_vector_index_verification_store.upsert_probe(
                vector_index_id=index.vector_index_id,
                tenant_id=index.tenant_id,
                verification_state="unverified",
                embedding_profile_attested=False,
                expected_dimension=profile.dimension,
                observed_dimension=state.dimension,
                expected_distance_metric=index.distance_metric,
                observed_distance_metric=state.distance_metric,
                observed_vector_type=state.vector_type,
                observed_points_count=state.points_count,
                selector_points_count=None,
                sample_size=0,
                sample_payload_keys=[],
                error="External collection attached; explicit compatibility verification is required",
                checked=False,
            )
    except (VectorTargetStoreError, EmbeddingProfileStoreError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Vector target/profile registry is unavailable",
        ) from exc
    except (VectorIndexStoreError, ExternalVectorIndexVerificationStoreError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except VectorStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"External collection probe failed: {exc}",
        ) from exc
    return ExternalVectorIndexAttachResponse(
        index=_vector_index_response(index),
        verification=_external_vector_index_verification_response(verification),
    )


@app.get(
    "/v1/admin/vector-indexes/{vector_index_id}/verification",
    response_model=ExternalVectorIndexVerificationResponse,
    tags=["System"],
    include_in_schema=False,
)
def get_external_vector_index_verification(
    vector_index_id: str,
    request: Request,
) -> ExternalVectorIndexVerificationResponse:
    _require_admin_proxy(request)
    try:
        verification = external_vector_index_verification_store.get(vector_index_id)
    except ExternalVectorIndexVerificationStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="External VectorIndex verification registry is unavailable",
        ) from exc
    if verification is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="External VectorIndex verification not found")
    return _external_vector_index_verification_response(verification)


@app.post(
    "/v1/admin/vector-indexes/{vector_index_id}/verify",
    response_model=ExternalVectorIndexVerificationResponse,
    tags=["System"],
    include_in_schema=False,
)
def verify_external_vector_index(
    vector_index_id: str,
    payload: ExternalVectorIndexVerifyRequest,
    request: Request,
) -> ExternalVectorIndexVerificationResponse:
    """Probe structural compatibility and separately require embedding-space attestation."""
    _require_admin_proxy(request)
    try:
        index = vector_index_store.get(vector_index_id)
        if index is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VectorIndex not found")
        if index.ownership_mode != "external_attached":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only external_attached VectorIndexes use P2-H verification",
            )
        target = vector_target_store.get(index.vector_target_id)
        profile = embedding_profile_store.get(index.embedding_profile_id)
        if target is None or profile is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="VectorIndex target/profile reference cannot be resolved",
            )
        previous = external_vector_index_verification_store.get(vector_index_id)
        attested = bool(payload.embedding_profile_attested or (previous and previous.embedding_profile_attested))
        adapter = _external_adapter(target)
        health = adapter.health()
        if not health.reachable:
            verification = external_vector_index_verification_store.upsert_probe(
                vector_index_id=index.vector_index_id,
                tenant_id=index.tenant_id,
                verification_state="unavailable",
                embedding_profile_attested=attested,
                expected_dimension=profile.dimension,
                observed_dimension=None,
                expected_distance_metric=index.distance_metric,
                observed_distance_metric=None,
                observed_vector_type=None,
                observed_points_count=None,
                selector_points_count=None,
                sample_size=0,
                sample_payload_keys=[],
                error=health.detail or "VectorTarget is unavailable",
            )
            vector_index_store.update_status(vector_index_id, "unavailable")
            return _external_vector_index_verification_response(verification)

        ref = VectorIndexRef(
            collection=index.collection,
            selector=VectorSelector(dict(index.selector)),
        )
        state = adapter.describe_index(ref)
        selector_count = adapter.count(ref) if state.exists else None
        samples = (
            adapter.sample(ref, limit=payload.sample_limit, include_vectors=False)
            if state.exists
            else []
        )
        evaluation = evaluate_external_index_probe(
            state=state,
            expected_dimension=profile.dimension,
            expected_distance_metric=index.distance_metric,
            embedding_profile_attested=attested,
        )
        verification = external_vector_index_verification_store.upsert_probe(
            vector_index_id=index.vector_index_id,
            tenant_id=index.tenant_id,
            verification_state=evaluation.verification_state,
            embedding_profile_attested=attested,
            expected_dimension=profile.dimension,
            observed_dimension=state.dimension,
            expected_distance_metric=index.distance_metric,
            observed_distance_metric=state.distance_metric,
            observed_vector_type=state.vector_type,
            observed_points_count=state.points_count,
            selector_points_count=selector_count,
            sample_size=len(samples),
            sample_payload_keys=sample_payload_keys(samples),
            error=evaluation.error,
        )
        vector_index_store.update_status(
            vector_index_id,
            "ready" if evaluation.verification_state == "compatible" else "unavailable",
        )
    except (VectorIndexStoreError, ExternalVectorIndexVerificationStoreError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except (VectorTargetStoreError, EmbeddingProfileStoreError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Vector target/profile registry is unavailable",
        ) from exc
    except VectorStoreError as exc:
        try:
            index = vector_index_store.get(vector_index_id)
            profile = embedding_profile_store.get(index.embedding_profile_id) if index else None
            if index and profile:
                external_vector_index_verification_store.upsert_probe(
                    vector_index_id=index.vector_index_id,
                    tenant_id=index.tenant_id,
                    verification_state="unavailable",
                    embedding_profile_attested=bool(payload.embedding_profile_attested),
                    expected_dimension=profile.dimension,
                    observed_dimension=None,
                    expected_distance_metric=index.distance_metric,
                    observed_distance_metric=None,
                    observed_vector_type=None,
                    observed_points_count=None,
                    selector_points_count=None,
                    sample_size=0,
                    sample_payload_keys=[],
                    error=str(exc),
                )
                vector_index_store.update_status(vector_index_id, "unavailable")
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"External VectorIndex verification probe failed: {exc}",
        ) from exc
    return _external_vector_index_verification_response(verification)


@app.post(
    "/v1/admin/snapshot-vector-bindings/external/verify",
    response_model=SnapshotVectorBindingRecordResponse,
    tags=["System"],
    include_in_schema=False,
)
def verify_external_snapshot_vector_binding(
    payload: ExternalSnapshotVectorBindingVerifyRequest,
    request: Request,
) -> SnapshotVectorBindingRecordResponse:
    """Verify or explicitly attest that an external logical index represents one Snapshot."""
    _require_admin_proxy(request)
    try:
        index = vector_index_store.get(payload.vector_index_id)
        if index is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VectorIndex not found")
        if index.ownership_mode != "external_attached":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="External Snapshot binding requires an external_attached VectorIndex",
            )
        verification = external_vector_index_verification_store.get(index.vector_index_id)
        if verification is None or verification.verification_state != "compatible" or index.status != "ready":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="External VectorIndex must be compatible and ready before Snapshot binding",
            )
        snapshot = repository_store.get_snapshot(payload.snapshot_id)
        if snapshot is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot not found")
        if str(snapshot.get("status") or "") != "completed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only completed immutable Snapshots can be bound to an external VectorIndex",
            )

        if payload.mode == "manual":
            if not payload.snapshot_attested:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="manual external Snapshot binding requires snapshot_attested=true",
                )
            evidence = {
                "proof_mode": "manual_attestation",
                "snapshot_attested": True,
                "external_index_verification_state": verification.verification_state,
                "embedding_profile_attested": verification.embedding_profile_attested,
            }
            method = "manual"
        else:
            target = vector_target_store.get(index.vector_target_id)
            if target is None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="VectorTarget not found")
            adapter = _external_adapter(target)
            ref = VectorIndexRef(
                collection=index.collection,
                selector=VectorSelector(dict(index.selector)),
            )
            selector_count = adapter.count(ref)
            samples = adapter.sample(ref, limit=payload.sample_limit, include_vectors=False)
            entries = repository_store.list_snapshot_indexable_entries(payload.snapshot_id)
            evaluation = evaluate_external_snapshot_probe(
                snapshot_id=payload.snapshot_id,
                project_id=str(snapshot.get("project_id") or ""),
                selector=index.selector,
                samples=samples,
                snapshot_entries=entries,
                selector_points_count=selector_count,
            )
            if not evaluation.compatible:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"EXTERNAL_SNAPSHOT_BINDING_UNVERIFIED: {evaluation.error}",
                )
            evidence = evaluation.evidence
            method = "external_probe"

        binding = snapshot_vector_binding_store.register_external_verification(
            snapshot_id=payload.snapshot_id,
            vector_index_id=index.vector_index_id,
            verification_method=method,
            verification_evidence=evidence,
        )
    except RepositoryStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Repository Snapshot registry is unavailable",
        ) from exc
    except (VectorIndexStoreError, ExternalVectorIndexVerificationStoreError, SnapshotVectorBindingStoreError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except VectorTargetStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VectorTarget registry is unavailable",
        ) from exc
    except VectorStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"External Snapshot binding probe failed: {exc}",
        ) from exc
    return _snapshot_vector_binding_response(binding)


def _snapshot_vector_binding_response(
    record: SnapshotVectorBindingRecord,
) -> SnapshotVectorBindingRecordResponse:
    return SnapshotVectorBindingRecordResponse(
        binding_id=record.binding_id,
        tenant_id=record.tenant_id,
        snapshot_id=record.snapshot_id,
        vector_index_id=record.vector_index_id,
        generation_id=record.generation_id,
        binding_source=record.binding_source,
        verification_state=record.verification_state,
        verification_method=record.verification_method,
        snapshot_fingerprint=record.snapshot_fingerprint,
        vector_index_identity_key=record.vector_index_identity_key,
        verification_evidence=record.verification_evidence,
        verified_at=record.verified_at,
        error=record.error,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@app.get(
    "/v1/admin/snapshot-vector-bindings",
    response_model=SnapshotVectorBindingListResponse,
    tags=["System"],
    include_in_schema=False,
)
def list_snapshot_vector_bindings(request: Request) -> SnapshotVectorBindingListResponse:
    _require_admin_proxy(request)
    try:
        records = snapshot_vector_binding_store.list(
            tenant_id=bootstrap_settings.snapshot_tenant_id.strip() or "vision-default"
        )
    except SnapshotVectorBindingStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL SnapshotVectorBinding registry is unavailable",
        ) from exc
    return SnapshotVectorBindingListResponse(
        bindings=[_snapshot_vector_binding_response(record) for record in records],
        total=len(records),
    )


@app.get(
    "/v1/admin/snapshot-vector-bindings/{binding_id}",
    response_model=SnapshotVectorBindingRecordResponse,
    tags=["System"],
    include_in_schema=False,
)
def get_snapshot_vector_binding(
    binding_id: str, request: Request
) -> SnapshotVectorBindingRecordResponse:
    _require_admin_proxy(request)
    try:
        record = snapshot_vector_binding_store.get(binding_id)
    except SnapshotVectorBindingStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL SnapshotVectorBinding registry is unavailable",
        ) from exc
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SnapshotVectorBinding not found",
        )
    return _snapshot_vector_binding_response(record)



def _project_vector_route_actor(request: Request) -> str:
    return str(
        getattr(request.state, "frontend_client_id", None)
        or getattr(request.state, "client_id", None)
        or "admin-dashboard"
    )


def _project_vector_route_candidate_response(
    project_id: str,
    candidate: RouteCandidateContext,
    *,
    active_binding_id: str | None,
) -> ProjectVectorRouteCandidateResponse:
    eligible = True
    reason: str | None = None
    try:
        project_vector_route_store.validate_candidate(project_id, candidate)
    except ProjectVectorRouteStoreError as exc:
        eligible = False
        reason = str(exc)
    routable = eligible and project_vector_route_store.current_runtime_routable(candidate)
    if eligible and not routable:
        reason = (
            "Current Vision runtime requires external payload keys: "
            "content, document_id, chunk_id. P3 hydration may later lift this restriction."
        )
    return ProjectVectorRouteCandidateResponse(
        binding_id=candidate.binding_id,
        snapshot_id=candidate.snapshot_id,
        generation_id=candidate.generation_id,
        generation_status=candidate.generation_status,
        vector_index_id=candidate.vector_index_id,
        ownership_mode=candidate.ownership_mode,
        binding_source=candidate.binding_source,
        verification_method=candidate.verification_method,
        vector_target_id=candidate.vector_target_id,
        embedding_profile_id=candidate.embedding_profile_id,
        vector_index_status=candidate.vector_index_status,
        vector_target_status=candidate.vector_target_status,
        embedding_profile_status=candidate.embedding_profile_status,
        external_verification_state=candidate.external_verification_state,
        payload_keys=list(candidate.sample_payload_keys),
        eligible=eligible,
        routable=routable,
        active=candidate.binding_id == active_binding_id,
        reason=reason,
    )


def _project_vector_route_response(
    record: ProjectVectorRouteRecord,
) -> ProjectVectorRouteRecordResponse:
    active = None
    if record.active_binding_id:
        candidate = project_vector_route_store.candidate_context(record.active_binding_id)
        if candidate is not None:
            active = _project_vector_route_candidate_response(
                record.project_id, candidate, active_binding_id=record.active_binding_id
            )
    return ProjectVectorRouteRecordResponse(
        project_id=record.project_id,
        tenant_id=record.tenant_id,
        active_binding_id=record.active_binding_id,
        routing_mode=record.routing_mode,
        revision=record.revision,
        selected_by=record.selected_by,
        selected_at=record.selected_at,
        reason=record.reason,
        active=active,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _require_vector_route_live_preflight(candidate: RouteCandidateContext) -> None:
    try:
        target = vector_target_store.get(candidate.vector_target_id)
    except VectorTargetStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VectorTarget registry is unavailable",
        ) from exc
    if target is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="VectorTarget not found")
    health = _external_adapter(target).health()
    if not health.reachable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"VECTOR_ROUTE_TARGET_UNAVAILABLE: {health.detail or 'Qdrant is unreachable'}",
        )


@app.get(
    "/v1/admin/projects/{project_id:path}/vector-route",
    response_model=ProjectVectorRouteRecordResponse,
    tags=["System"],
    include_in_schema=False,
)
def get_project_vector_route(project_id: str, request: Request) -> ProjectVectorRouteRecordResponse:
    _require_admin_proxy(request)
    try:
        record = project_vector_route_store.get(project_id)
    except ProjectVectorRouteStoreError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="PROJECT_VECTOR_ROUTE_REQUIRED: project has no P2-I retrieval route",
        )
    return _project_vector_route_response(record)


@app.get(
    "/v1/admin/projects/{project_id:path}/vector-route/candidates",
    response_model=ProjectVectorRouteCandidateListResponse,
    tags=["System"],
    include_in_schema=False,
)
def list_project_vector_route_candidates(
    project_id: str, request: Request
) -> ProjectVectorRouteCandidateListResponse:
    _require_admin_proxy(request)
    try:
        route = project_vector_route_store.get(project_id)
        bindings = snapshot_vector_binding_store.list(
            tenant_id=bootstrap_settings.snapshot_tenant_id.strip() or "vision-default"
        )
        candidates: list[ProjectVectorRouteCandidateResponse] = []
        for binding in bindings:
            candidate = project_vector_route_store.candidate_context(binding.binding_id)
            if candidate is None or candidate.snapshot_project_id != project_id:
                continue
            candidates.append(
                _project_vector_route_candidate_response(
                    project_id,
                    candidate,
                    active_binding_id=route.active_binding_id if route else None,
                )
            )
    except (ProjectVectorRouteStoreError, SnapshotVectorBindingStoreError) as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return ProjectVectorRouteCandidateListResponse(
        project_id=project_id,
        active_binding_id=route.active_binding_id if route else None,
        routing_mode=route.routing_mode if route else "managed_auto",
        revision=route.revision if route else 0,
        candidates=candidates,
    )


@app.put(
    "/v1/admin/projects/{project_id:path}/vector-route",
    response_model=ProjectVectorRouteRecordResponse,
    tags=["System"],
    include_in_schema=False,
)
def set_project_vector_route(
    project_id: str,
    payload: ProjectVectorRouteWriteRequest,
    request: Request,
) -> ProjectVectorRouteRecordResponse:
    _require_admin_proxy(request)
    try:
        candidate = project_vector_route_store.candidate_context(payload.binding_id)
        if candidate is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SnapshotVectorBinding not found")
        project_vector_route_store.validate_candidate(project_id, candidate)
        if not project_vector_route_store.current_runtime_routable(candidate):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="VECTOR_ROUTE_NOT_ROUTABLE: current Vision runtime cannot consume this external payload contract",
            )
        _require_vector_route_live_preflight(candidate)
        record = project_vector_route_store.set_route(
            project_id=project_id,
            binding_id=payload.binding_id,
            routing_mode=payload.routing_mode,
            expected_revision=payload.expected_revision,
            actor=_project_vector_route_actor(request),
            reason=payload.reason,
        )
    except ProjectVectorRouteConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ProjectVectorRouteStoreError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _project_vector_route_response(record)


@app.delete(
    "/v1/admin/projects/{project_id:path}/vector-route",
    response_model=ProjectVectorRouteRecordResponse,
    tags=["System"],
    include_in_schema=False,
)
def clear_project_vector_route(
    project_id: str,
    payload: ProjectVectorRouteClearRequest,
    request: Request,
) -> ProjectVectorRouteRecordResponse:
    _require_admin_proxy(request)
    try:
        record = project_vector_route_store.clear_route(
            project_id=project_id,
            expected_revision=payload.expected_revision,
            actor=_project_vector_route_actor(request),
            reason=payload.reason,
        )
    except ProjectVectorRouteConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ProjectVectorRouteStoreError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _project_vector_route_response(record)


@app.get(
    "/v1/admin/projects/{project_id:path}/vector-route/events",
    response_model=ProjectVectorRouteEventListResponse,
    tags=["System"],
    include_in_schema=False,
)
def list_project_vector_route_events(
    project_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> ProjectVectorRouteEventListResponse:
    _require_admin_proxy(request)
    try:
        events = project_vector_route_store.list_events(project_id, limit=limit)
    except ProjectVectorRouteStoreError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return ProjectVectorRouteEventListResponse(
        project_id=project_id,
        events=[
            ProjectVectorRouteEventResponse(
                event_id=item.event_id,
                project_id=item.project_id,
                tenant_id=item.tenant_id,
                from_binding_id=item.from_binding_id,
                to_binding_id=item.to_binding_id,
                routing_mode=item.routing_mode,
                actor=item.actor,
                reason=item.reason,
                revision=item.revision,
                created_at=item.created_at,
            )
            for item in events
        ],
    )


@app.get(
    "/v1/admin/models",
    response_model=ModelListResponse,
    tags=["System"],
    summary="List every discovered generation model and its API access policy",
)
def list_admin_models(request: Request) -> ModelListResponse:
    _require_admin_proxy(request)
    models = generation_router.models()
    default_model_id = generation_router.default_model_id
    return ModelListResponse(
        catalog_revision=model_catalog_revision(default_model_id, models),
        default_model_id=default_model_id,
        checked_at=datetime.now(timezone.utc),
        models=models,
    )


def _ai_provider_record(provider: AIProvider) -> AIProviderRecord:
    models = [
        item.model_name
        for item in ai_provider_store.discovered_models()
        if item.provider_id == provider.provider_id
    ]
    return AIProviderRecord(
        provider_id=provider.provider_id,
        name=provider.name,
        protocol=provider.protocol,
        base_url=provider.base_url,
        auth_type=provider.auth_type,
        api_key_configured=provider.api_key_configured,
        api_key_hint=provider.api_key_hint,
        enabled=provider.enabled,
        deployment_type=provider.deployment_type,
        chat_processing_mode=provider.chat_processing_mode,
        status=provider.status,
        error=provider.error,
        latency_ms=provider.latency_ms,
        model_count=provider.model_count,
        models=models,
        last_checked_at=provider.last_checked_at,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


def _provider_store_unavailable(exc: AIProviderStoreError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=str(exc),
    )


@app.get(
    "/v1/admin/ai-providers",
    response_model=AIProviderListResponse,
    tags=["System"],
    summary="List administrator-managed AI inference providers",
)
def list_ai_providers(
    request: Request,
    refresh: bool = Query(default=False),
) -> AIProviderListResponse:
    _require_admin_proxy(request)
    try:
        providers = ai_provider_store.list()
        if refresh:
            for provider in providers:
                if provider.enabled:
                    ai_provider_registry.discover(provider.provider_id)
            providers = ai_provider_store.list()
        records = [_ai_provider_record(provider) for provider in providers]
    except AIProviderStoreError as exc:
        raise _provider_store_unavailable(exc) from exc
    return AIProviderListResponse(providers=records, total=len(records))


@app.post(
    "/v1/admin/ai-providers",
    response_model=AIProviderRecord,
    status_code=status.HTTP_201_CREATED,
    tags=["System"],
    summary="Register an AI provider and automatically discover its models",
)
def create_ai_provider(
    payload: AIProviderWriteRequest,
    request: Request,
) -> AIProviderRecord:
    _require_admin_proxy(request)
    if payload.auth_type != "none" and not payload.api_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="api_key is required for a new authenticated provider",
        )
    try:
        base_url = payload.resolved_base_url()
        protocol = (
            ai_provider_registry.detect_protocol(
                base_url,
                payload.auth_type,
                payload.api_key,
            )
            if payload.protocol == "auto"
            else payload.protocol
        )
        provider = ai_provider_store.create(
            name=payload.name,
            protocol=protocol,
            base_url=base_url,
            auth_type=payload.auth_type,
            api_key=payload.api_key,
            enabled=payload.enabled,
            deployment_type=payload.deployment_type,
            chat_processing_mode=payload.chat_processing_mode,
        )
        if provider.enabled:
            provider = ai_provider_registry.discover(provider.provider_id)
        return _ai_provider_record(provider)
    except AIProviderStoreError as exc:
        raise _provider_store_unavailable(exc) from exc


@app.post(
    "/v1/admin/ai-providers/scan-ollama",
    response_model=OllamaScanResponse,
    tags=["System"],
    summary="Refresh administrator-configured Ollama-compatible model runtimes",
)
def scan_known_ollama_servers(request: Request) -> OllamaScanResponse:
    """Probe only endpoints already saved by an administrator; never guess LAN hosts or ports."""

    _require_admin_proxy(request)
    legacy_backendai_url = runtime_network_store.backendai_base_url().rstrip("/")
    candidates: dict[str, str] = {
        legacy_backendai_url: "관리자 저장 기본 모델 실행 대상",
    }
    try:
        existing_provider_rows = ai_provider_store.list()
        existing_providers = {
            provider.base_url.rstrip("/"): provider
            for provider in existing_provider_rows
        }
        for provider in existing_provider_rows:
            if provider.enabled and provider.protocol == "ollama":
                candidates.setdefault(
                    provider.base_url.rstrip("/"),
                    f"등록된 모델 실행 대상 · {provider.name}",
                )
    except AIProviderStoreError as exc:
        raise _provider_store_unavailable(exc) from exc

    def probe(base_url: str) -> Any:
        return ai_provider_registry.probe_ollama(
            base_url,
            timeout_seconds=3,
        )

    with ThreadPoolExecutor(max_workers=min(8, len(candidates))) as executor:
        futures = {
            base_url: executor.submit(probe, base_url)
            for base_url in candidates
        }
        results = {
            base_url: future.result()
            for base_url, future in futures.items()
        }

    targets: list[OllamaScanTarget] = []
    registered_count = 0
    for base_url, source in candidates.items():
        result = results[base_url]
        provider_id: str | None = None
        registered = False
        if result.models:
            existing = existing_providers.get(base_url)
            try:
                if existing is not None:
                    ai_provider_store.update_discovery(
                        existing.provider_id,
                        result,
                    )
                    provider_id = existing.provider_id
                    registered = True
                elif base_url == legacy_backendai_url:
                    provider_id = settings.backendai_public_model_id
                    registered = True
                else:
                    deployment_type = (
                        "local"
                        if base_url.startswith(
                            (
                                "http://host.docker.internal:",
                                "http://127.0.0.1:",
                                "http://localhost:",
                            )
                        )
                        else "remote_server"
                    )
                    provider = ai_provider_store.create(
                        name=f"Ollama · {source}",
                        protocol="ollama",
                        base_url=base_url,
                        auth_type="none",
                        api_key=None,
                        enabled=True,
                        deployment_type=deployment_type,
                    )
                    ai_provider_store.update_discovery(
                        provider.provider_id,
                        result,
                    )
                    provider_id = provider.provider_id
                    registered = True
                    existing_providers[base_url] = provider
            except AIProviderStoreError as exc:
                raise _provider_store_unavailable(exc) from exc
        if registered:
            registered_count += 1
        targets.append(
            OllamaScanTarget(
                source=source,
                base_url=base_url,
                status=result.status,
                models=result.models,
                skipped_non_chat_models=result.skipped_models,
                latency_ms=result.latency_ms,
                error=result.error,
                registered=registered,
                provider_id=provider_id,
            )
        )
    return OllamaScanResponse(
        checked_at=datetime.now(timezone.utc),
        targets=targets,
        discovered_servers=sum(
            target.status in {"online", "degraded"} for target in targets
        ),
        registered_providers=registered_count,
        chat_models=sum(len(target.models) for target in targets),
    )


@app.put(
    "/v1/admin/ai-providers/{provider_id}",
    response_model=AIProviderRecord,
    tags=["System"],
    summary="Update an AI provider and repeat model discovery",
)
def update_ai_provider(
    provider_id: str,
    payload: AIProviderWriteRequest,
    request: Request,
) -> AIProviderRecord:
    _require_admin_proxy(request)
    try:
        current = ai_provider_store.get(provider_id, with_secret=True)
        if current is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="AI provider was not found",
            )
        if (
            not payload.enabled
            and generation_router.default_model_id.startswith(
                f"provider:{provider_id}:"
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Select another default model before disabling this provider",
            )
        if (
            payload.auth_type != "none"
            and not payload.api_key
            and (payload.clear_api_key or not current.api_key_configured)
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="api_key is required for the selected auth_type",
            )
        base_url = payload.resolved_base_url()
        effective_api_key = (
            None
            if payload.clear_api_key or payload.auth_type == "none"
            else payload.api_key or current.api_key
        )
        protocol = (
            ai_provider_registry.detect_protocol(
                base_url,
                payload.auth_type,
                effective_api_key,
            )
            if payload.protocol == "auto"
            else payload.protocol
        )
        provider = ai_provider_store.update(
            provider_id,
            name=payload.name,
            protocol=protocol,
            base_url=base_url,
            auth_type=payload.auth_type,
            api_key=payload.api_key,
            clear_api_key=payload.clear_api_key or payload.auth_type == "none",
            enabled=payload.enabled,
            deployment_type=payload.deployment_type,
            chat_processing_mode=payload.chat_processing_mode,
        )
        if provider is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="AI provider was not found",
            )
        provider = ai_provider_registry.discover(provider_id)
        return _ai_provider_record(provider)
    except AIProviderStoreError as exc:
        raise _provider_store_unavailable(exc) from exc


@app.post(
    "/v1/admin/ai-providers/{provider_id}/discover",
    response_model=AIProviderRecord,
    tags=["System"],
    summary="Probe one AI provider and refresh its model catalog",
)
def discover_ai_provider(
    provider_id: str,
    request: Request,
) -> AIProviderRecord:
    _require_admin_proxy(request)
    try:
        return _ai_provider_record(ai_provider_registry.discover(provider_id))
    except AIProviderStoreError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="AI provider was not found",
            ) from exc
        raise _provider_store_unavailable(exc) from exc


@app.delete(
    "/v1/admin/ai-providers/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["System"],
    summary="Delete an AI provider and its discovered model catalog",
)
def delete_ai_provider(provider_id: str, request: Request) -> Response:
    _require_admin_proxy(request)
    if generation_router.default_model_id.startswith(f"provider:{provider_id}:"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Select another default model before deleting this provider",
        )
    try:
        deleted = ai_provider_store.delete(provider_id)
    except AIProviderStoreError as exc:
        raise _provider_store_unavailable(exc) from exc
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AI provider was not found",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.put(
    "/v1/admin/models/access",
    response_model=ModelAccessUpdateResponse,
    tags=["System"],
    summary="Enable or disable Frontend API access for one generation model",
)
def update_model_access(
    payload: ModelAccessUpdateRequest,
    request: Request,
) -> ModelAccessUpdateResponse:
    _require_admin_proxy(request)
    models = {
        model.model_id: model for model in generation_router.models()
    }
    if payload.model_id not in models:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Discovered generation model was not found",
        )
    if (
        not payload.enabled
        and payload.model_id == generation_router.default_model_id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Select another default model before disabling this model",
        )
    try:
        row = model_access_store.set_enabled(
            payload.model_id, payload.enabled
        )
    except ModelAccessPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL model access policy store is unavailable",
        ) from exc
    refreshed = {
        model.model_id: model for model in generation_router.models()
    }
    return ModelAccessUpdateResponse(
        model=refreshed[payload.model_id],
        updated_at=row["updated_at"],
    )


@app.put(
    "/v1/admin/service-settings",
    response_model=RuntimeServiceSettingsResponse,
    tags=["System"],
    include_in_schema=False,
)
def update_runtime_service_settings(
    payload: RuntimeServiceSettingsUpdateRequest,
    request: Request,
) -> RuntimeServiceSettingsResponse:
    _require_admin_proxy(request)
    selectable_models = {
        model.model_id: model for model in generation_router.models()
    }
    allowed_defaults = {
        model_id for model_id, model in selectable_models.items()
        if model.enabled
    }
    if payload.default_model_id not in allowed_defaults:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="default_model_id must be one of the model IDs returned by GET /v1/models",
        )
    if (
        payload.default_model_id == settings.groq_public_model_id
        and not payload.groq.enabled
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="groq-default cannot be selected while Groq is disabled",
        )
    try:
        target = None
        if payload.vector.vector_target_id:
            target = vector_target_store.get(payload.vector.vector_target_id)
            if target is None or not target.enabled:
                raise VectorTargetStoreError("Selected VectorTarget is unavailable")
            parsed_target = urlsplit(target.endpoint)
            target_port = parsed_target.port or (443 if parsed_target.scheme == "https" else 80)
            if (parsed_target.hostname or "") != payload.vector.host or target_port != payload.vector.port:
                target = vector_target_store.upsert_qdrant(
                    endpoint=f"http://{payload.vector.host}:{payload.vector.port}",
                    name=f"Qdrant {payload.vector.host}:{payload.vector.port}",
                )
        else:
            target = vector_target_store.upsert_qdrant(
                endpoint=f"http://{payload.vector.host}:{payload.vector.port}",
                name=f"Qdrant {payload.vector.host}:{payload.vector.port}",
            )
        existing_profile = None
        if payload.vector.embedding_profile_id:
            existing_profile = embedding_profile_store.get(
                payload.vector.embedding_profile_id
            )
        profile = embedding_profile_store.upsert(
            name=(
                existing_profile.name
                if existing_profile is not None
                else payload.vector.embedding_model_id
            ),
            deployment=payload.vector.embedding_deployment,
            provider=payload.vector.embedding_provider,
            base_url=payload.vector.embedding_base_url,
            model=payload.vector.embedding_model,
            model_id=payload.vector.embedding_model_id,
            dimension=payload.vector.embedding_dimension,
            batch_size=payload.vector.embedding_batch_size,
            credential_ref=(
                existing_profile.credential_ref
                if existing_profile is not None
                else None
            ),
        )
        value = runtime_service_store.update(
            groq_enabled=payload.groq.enabled,
            groq_base_url=payload.groq.base_url,
            groq_model=payload.groq.model,
            default_model_id=payload.default_model_id,
            vector_target_id=target.vector_target_id,
            embedding_profile_id=profile.embedding_profile_id,
            vector_collection=payload.vector.collection,
            index_version=payload.vector.index_version,
        )
    except (
        RuntimeServiceSettingsError,
        VectorTargetStoreError,
        EmbeddingProfileStoreError,
        ValueError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL runtime service settings, VectorTarget, or EmbeddingProfile registry is unavailable",
        ) from exc
    runtime_settings_resolver.invalidate()
    generation_router.invalidate_groq_status()
    generation_router.invalidate_nvidia_status()
    generation_router.invalidate_backendai_status()
    return _runtime_service_settings_response(value)


@app.get(
    "/v1/admin/embedding-artifacts",
    response_model=OfflineEmbeddingArtifactListResponse,
    tags=["Projects"],
    include_in_schema=False,
)
def list_offline_embedding_artifacts(
    request: Request,
) -> OfflineEmbeddingArtifactListResponse:
    _require_admin_proxy(request)
    rows = offline_embedding_importer.list_artifacts()
    artifacts = [
        OfflineEmbeddingArtifactSummary.model_validate(row) for row in rows
    ]
    return OfflineEmbeddingArtifactListResponse(
        checked_at=datetime.now(timezone.utc),
        root_available=offline_embedding_importer.root.is_dir(),
        artifacts=artifacts,
        total=len(artifacts),
        ready=sum(
            artifact.compatible
            and not artifact.imported
            and artifact.error is None
            for artifact in artifacts
        ),
        imported=sum(artifact.imported for artifact in artifacts),
    )


@app.post(
    "/v1/admin/embedding-artifacts/{artifact_id}/import",
    response_model=RepositoryIndexJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Projects"],
    include_in_schema=False,
)
def import_offline_embedding_artifact(
    artifact_id: str,
    request: Request,
) -> RepositoryIndexJobResponse:
    _require_admin_proxy(request)
    try:
        _artifact, source = offline_embedding_importer.prepare_import(
            artifact_id
        )
        row = repository_store.create_job(
            f"job_{uuid4().hex}", source, True
        )
    except OfflineEmbeddingArtifactError as exc:
        status_code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in str(exc).lower()
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except RepositoryStoreError as exc:
        if "active indexing job" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        raise _repository_store_error(exc) from exc
    _enqueue_worker_task(
        "offline.import",
        {
            "job_id": row["job_id"],
            "artifact_id": artifact_id,
        },
        dedupe_key=f"offline.import:{row['job_id']}",
        repository_job_id=row["job_id"],
    )
    return _repository_job_response(row)


def _safe_tree_prefix(path: str) -> str:
    normalized = path.replace("\\", "/").strip("/")
    if (
        any(part in {"", ".", ".."} for part in normalized.split("/"))
        and normalized
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path must be a safe project-relative prefix",
        )
    return normalized


@app.get(
    "/v1/repositories",
    response_model=RepositoryBrowserListResponse,
    tags=["Projects"],
    summary="List Git sources and compare source HEAD with the active DB snapshot",
)
def list_repository_browser_items() -> RepositoryBrowserListResponse:
    try:
        rows = repository_store.list_sources()
    except RepositoryStoreError as exc:
        raise _repository_store_error(exc) from exc
    repositories: list[RepositoryBrowserItem] = []
    for row in rows:
        source_version: dict[str, Any] | None = None
        error: str | None = None
        try:
            source_version = repository_indexer.inspect_source(row)
        except Exception as exc:
            error = str(exc)[:500] or exc.__class__.__name__
        try:
            indexed = project_store.get_version(row["project_id"])
        except ProjectStoreError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="PostgreSQL project registry is unavailable",
            ) from exc
        source_revision = (
            str(source_version["revision"]) if source_version else None
        )
        indexed_revision = (
            str(indexed.get("git_commit_sha"))
            if indexed and indexed.get("git_commit_sha")
            else None
        )
        if source_version is None:
            version_status = "unavailable"
        elif indexed_revision is None:
            version_status = "not_indexed"
        elif source_revision == indexed_revision:
            version_status = "current"
        else:
            version_status = "different"
        encoded_source_id = quote(str(row["source_id"]), safe="")
        encoded_project_id = quote(str(row["project_id"]), safe="")
        repositories.append(
            RepositoryBrowserItem(
                source_id=row["source_id"],
                project_id=row["project_id"],
                project_name=str(row["project_id"]).rsplit("/", 1)[-1],
                source_type=row["source_type"],
                repository_url=row.get("repository_url"),
                default_branch=row.get("default_branch"),
                enabled=bool(row["enabled"]),
                source_available=source_version is not None,
                source_revision=source_revision,
                source_short_revision=(
                    source_revision[:7] if source_revision else None
                ),
                source_branch=(
                    source_version.get("branch") if source_version else None
                ),
                source_dirty=(
                    source_version.get("dirty") if source_version else None
                ),
                source_committed_at=(
                    source_version.get("committed_at")
                    if source_version
                    else None
                ),
                indexed_revision=indexed_revision,
                indexed_short_revision=(
                    indexed_revision[:7] if indexed_revision else None
                ),
                indexed_snapshot_id=(
                    indexed.get("current_snapshot_id") if indexed else None
                ),
                index_status=(
                    str(indexed.get("index_status") or "not_indexed")
                    if indexed
                    else "not_indexed"
                ),
                version_status=version_status,
                source_tree_url=(
                    f"/v1/repositories/{encoded_source_id}/tree"
                ),
                indexed_tree_url=(
                    f"/v1/projects/{encoded_project_id}/tree"
                ),
                error=error,
            )
        )
    return RepositoryBrowserListResponse(
        repositories=repositories,
        total=len(repositories),
        generated_at=datetime.now(timezone.utc),
    )


@app.get(
    "/v1/repositories/{source_id}/tree",
    response_model=RepositorySourceTreeResponse,
    tags=["Projects"],
    summary="Read the tracked folder tree from the Backend Git checkout HEAD",
)
def get_repository_source_tree(
    source_id: str,
    path: str = Query(default="", max_length=4096),
) -> RepositorySourceTreeResponse:
    normalized_path = _safe_tree_prefix(path)
    try:
        source = repository_store.get_source(source_id)
    except RepositoryStoreError as exc:
        raise _repository_store_error(exc) from exc
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repository source was not found",
        )
    try:
        version, rows = repository_indexer.list_source_tree(
            source,
            normalized_path,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Repository source checkout is unavailable: {str(exc)[:500]}",
        ) from exc
    entries = [ProjectTreeEntry.model_validate(row) for row in rows]
    return RepositorySourceTreeResponse(
        source_id=source["source_id"],
        project_id=source["project_id"],
        repository_url=source.get("repository_url"),
        revision=version["revision"],
        branch=version.get("branch"),
        dirty=version.get("dirty"),
        committed_at=version.get("committed_at"),
        prefix=normalized_path,
        entries=entries,
        total=len(entries),
    )


@app.get(
    "/v1/admin/repository-sources",
    response_model=RepositorySourceListResponse,
    tags=["Projects"],
    summary="List registered repository sources",
)
def list_repository_sources(request: Request) -> RepositorySourceListResponse:
    _require_admin_proxy(request)
    try:
        rows = repository_store.list_sources()
    except RepositoryStoreError as exc:
        raise _repository_store_error(exc) from exc
    sources = [RepositorySourceRecord.model_validate(row) for row in rows]
    return RepositorySourceListResponse(sources=sources, total=len(sources))


@app.post(
    "/v1/admin/repository-sources",
    response_model=RepositorySourceRecord,
    status_code=status.HTTP_201_CREATED,
    tags=["Projects"],
    summary="Register or update a local/Git repository source",
)
def upsert_repository_source(
    payload: RepositorySourceWriteRequest,
    request: Request,
) -> RepositorySourceRecord:
    _require_admin_proxy(request)
    try:
        row = repository_store.upsert_source(payload.model_dump())
    except RepositoryStoreError as exc:
        raise _repository_store_error(exc) from exc
    return RepositorySourceRecord.model_validate(row)


@app.post(
    "/v1/admin/repository-sources/{source_id}/index",
    response_model=RepositoryIndexJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Projects"],
    summary="Queue a repository snapshot and configured index generation",
)
def queue_repository_index(
    source_id: str,
    payload: RepositoryIndexRequest,
    request: Request,
) -> RepositoryIndexJobResponse:
    _require_admin_proxy(request)
    try:
        source = repository_store.get_source(source_id)
        if source is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Repository source was not found",
            )
        row = repository_store.create_job(
            f"job_{uuid4().hex}", source, payload.force
        )
    except RepositoryStoreError as exc:
        if "active indexing job" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        raise _repository_store_error(exc) from exc
    _enqueue_worker_task(
        "repository.index",
        {"job_id": row["job_id"]},
        dedupe_key=f"repository.index:{row['job_id']}",
        repository_job_id=row["job_id"],
    )
    return _repository_job_response(row)


@app.post(
    "/v1/admin/indexing-jobs/{job_id}/resume",
    response_model=RepositoryIndexJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Projects"],
    summary="Resume an interrupted repository embedding generation",
)
def resume_repository_index(
    job_id: str,
    request: Request,
) -> RepositoryIndexJobResponse:
    _require_admin_proxy(request)
    try:
        existing = repository_store.get_job(job_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Repository indexing job was not found",
            )
        row = repository_store.prepare_job_resume(job_id)
    except RepositoryStoreError as exc:
        if (
            "cannot be resumed" in str(exc)
            or "still appears to be running" in str(exc)
            or "no durable snapshot checkpoint" in str(exc)
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        raise _repository_store_error(exc) from exc
    _enqueue_worker_task(
        "repository.resume",
        {"job_id": row["job_id"]},
        dedupe_key=f"repository.resume:{row['job_id']}",
        repository_job_id=row["job_id"],
    )
    return _repository_job_response(row)


def _read_project_briefing(
    project_id: str,
    commit_id: str | None,
    request: Request,
) -> ProjectBriefingResponse:
    request_id = _request_id(request)
    telemetry_client_id = _frontend_client_id(request, project_id)
    started = perf_counter()
    _safe_record_communication_event(
        request_id=request_id,
        channel="rag",
        direction="fastapi_to_vectordb",
        phase="rag.briefing.request",
        status="started",
        client_id=telemetry_client_id,
        project_id=project_id,
        provider="rag_lab",
        details={
            "endpoint": f"{rag_lab_client.base_url}/briefing",
            "requested_commit_id": commit_id,
        },
    )
    try:
        binding = rag_lab_client.resolve_project(project_id)
        value = rag_lab_client.briefing(binding.external_project_id)
    except RagLabError as exc:
        _safe_record_communication_event(
            request_id=request_id,
            channel="rag",
            direction="vectordb_to_fastapi",
            phase="rag.briefing.response",
            status="error",
            client_id=telemetry_client_id,
            project_id=project_id,
            status_code=exc.status_code,
            duration_ms=round((perf_counter() - started) * 1000),
            provider="rag_lab",
            error=str(exc),
        )
        raise _service_error(exc) from exc
    result = build_project_briefing_response(project_id, commit_id, binding, value)
    _safe_record_communication_event(
        request_id=request_id,
        channel="rag",
        direction="vectordb_to_fastapi",
        phase="rag.briefing.response",
        status="success",
        client_id=telemetry_client_id,
        project_id=project_id,
        status_code=200,
        duration_ms=round((perf_counter() - started) * 1000),
        provider="rag_lab",
        source_count=len(result.references),
        details={
            "external_project_id": result.external_project_id,
            "revision_status": result.revision_status,
            "outdated": result.outdated,
        },
    )
    return result


@app.get(
    "/v1/projects/{project_id:path}/briefing",
    response_model=ProjectBriefingResponse,
    responses=ERROR_RESPONSES,
    tags=["Projects"],
    summary="Read the generated external RAG project briefing",
)
def get_project_briefing(
    project_id: str,
    request: Request,
    commit_id: str | None = Query(default=None, min_length=7, max_length=64),
) -> ProjectBriefingResponse:
    return _read_project_briefing(project_id, commit_id, request)


@app.get(
    "/v1/briefing",
    response_model=ProjectBriefingResponse,
    responses=ERROR_RESPONSES,
    tags=["Projects"],
    summary="Compatibility route for the generated project briefing",
)
def get_project_briefing_compatibility(
    request: Request,
    project_id: str = Query(..., min_length=1, max_length=255),
    commit_id: str | None = Query(default=None, min_length=7, max_length=64),
) -> ProjectBriefingResponse:
    return _read_project_briefing(project_id, commit_id, request)


@app.get(
    "/v1/projects/{project_id:path}/tree",
    response_model=ProjectTreeResponse,
    tags=["Projects"],
    summary="Read the active backend snapshot folder tree",
)
def get_project_tree(
    project_id: str,
    path: str = Query(default="", max_length=4096),
) -> ProjectTreeResponse:
    normalized_path = _safe_tree_prefix(path)
    try:
        active, rows = repository_store.list_tree(project_id, normalized_path)
    except RepositoryStoreError as exc:
        if "no current snapshot" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        raise _repository_store_error(exc) from exc
    entries = [ProjectTreeEntry.model_validate(row) for row in rows]
    return ProjectTreeResponse(
        project_id=project_id,
        snapshot_id=active["snapshot_id"],
        generation_id=active["generation_id"],
        revision=active.get("revision"),
        prefix=normalized_path,
        entries=entries,
        total=len(entries),
    )


@app.get(
    "/v1/projects/{project_id:path}/file",
    response_model=ProjectFileResponse,
    tags=["Projects"],
    summary="Read text from the active backend snapshot",
)
def get_project_file(
    project_id: str,
    path: str = Query(..., min_length=1, max_length=4096),
) -> ProjectFileResponse:
    normalized_path = path.replace("\\", "/").strip("/")
    if any(part in {"", ".", ".."} for part in normalized_path.split("/")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="path must be a safe project-relative file path",
        )
    try:
        active, row = repository_store.get_file(project_id, normalized_path)
    except RepositoryStoreError as exc:
        if "no current snapshot" in str(exc):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        raise _repository_store_error(exc) from exc
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Text file was not found in the active snapshot",
        )
    return ProjectFileResponse(
        project_id=project_id,
        snapshot_id=active["snapshot_id"],
        generation_id=active["generation_id"],
        **row,
    )


@app.get(
    "/v1/projects/{project_id:path}/index-validation",
    response_model=VectorIndexValidationResponse,
    tags=["Projects"],
    summary="Compare active PostgreSQL chunk mappings with Qdrant",
)
def validate_project_index(project_id: str) -> VectorIndexValidationResponse:
    runtime = _resolve_project_vector_runtime(project_id)
    binding: SnapshotVectorBindingRecord = runtime["binding"]
    generation = runtime["generation"]
    if generation is None or binding.generation_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="INDEX_VALIDATION_NOT_APPLICABLE: active project route is external and has no managed Generation ledger",
        )
    try:
        postgres_chunks = repository_store.generation_chunk_count(binding.generation_id)
        qdrant_chunks = runtime["vector_store"].count_generation(
            project_id, binding.generation_id
        )
    except RepositoryStoreError as exc:
        raise _repository_store_error(exc) from exc
    except VectorStoreError as exc:
        raise _service_error(exc) from exc
    return VectorIndexValidationResponse(
        project_id=project_id,
        snapshot_id=binding.snapshot_id,
        generation_id=binding.generation_id,
        postgres_chunks=postgres_chunks,
        qdrant_chunks=qdrant_chunks,
        consistent=postgres_chunks == qdrant_chunks,
        checked_at=datetime.now(timezone.utc),
    )


@app.post("/v1/documents/ingest", response_model=IngestResponse, tags=["Documents"])
def ingest_documents(payload: IngestRequest) -> IngestResponse:
    return _ingest_documents(payload)


def _ingest_documents(
    payload: IngestRequest,
    project_metadata: dict[str, Any] | None = None,
) -> IngestResponse:
    chunks_stored = 0
    metadata_records_stored = 0
    providers: set[str] = set()
    try:
        for document in payload.documents:
            stored, provider = _ingest_one_document(payload.project_id, document)
            chunks_stored += stored
            if provider:
                providers.add(provider)
            if document.metadata:
                try:
                    metadata_store.upsert(
                        MetadataUpsertRequest(
                            project_id=payload.project_id,
                            scope="document",
                            entity_id=document.document_id,
                            source="document-ingest",
                            metadata=document.metadata,
                        )
                    )
                    metadata_records_stored += 1
                except MetadataStoreError as exc:
                    raise _metadata_store_error() from exc
        if project_metadata:
            try:
                metadata_store.upsert(
                    MetadataUpsertRequest(
                        project_id=payload.project_id,
                        scope="project",
                        source="project-document-ingest",
                        metadata=project_metadata,
                    )
                )
                metadata_records_stored += 1
            except MetadataStoreError as exc:
                raise _metadata_store_error() from exc
    except (ServiceError, VectorStoreError) as exc:
        raise _service_error(exc) from exc
    return IngestResponse(
        project_id=payload.project_id,
        documents_received=len(payload.documents),
        chunks_stored=chunks_stored,
        embedding_provider=",".join(sorted(providers)) or settings.embedding_provider,
        metadata_records_stored=metadata_records_stored,
    )


def _ingest_one_document(project_id: str, document: DocumentInput) -> tuple[int, str]:
    chunk_specs = chunk_text_with_metadata(
        document.text, settings.chunk_size, settings.chunk_overlap
    )
    document_hash = hashlib.sha256(document.text.encode("utf-8")).hexdigest()[:16]
    embedded_chunks: list[dict[str, Any]] = []
    for batch_start in range(0, len(chunk_specs), settings.embedding_batch_size):
        batch = chunk_specs[batch_start : batch_start + settings.embedding_batch_size]
        embeddings = embedding_service.embed_many(
            [str(item["content"]) for item in batch], input_type="passage"
        )
        for offset, (item, embedding) in enumerate(zip(batch, embeddings)):
            index = batch_start + offset + 1
            embedded_chunks.append(
                {
                    "chunk_id": (
                        f"{project_id}:{document.document_id}:{document_hash}#chunk-{index}"
                    ),
                    "content": str(item["content"]),
                    "line_start": int(item["line_start"]),
                    "line_end": int(item["line_end"]),
                    "embedding": embedding.vector,
                    "embedding_provider": embedding.provider,
                    "embedding_model": embedding.model,
                }
            )
    try:
        project_store.save_document(project_id, document, embedded_chunks)
    except ProjectStoreError as exc:
        raise _project_store_error() from exc
    stored = vector_store.replace_document(
        project_id,
        document.document_id,
        document.path,
        document.language,
        embedded_chunks,
        document.metadata,
    )
    provider = embedded_chunks[0]["embedding_provider"] if embedded_chunks else ""
    return stored, provider


def _ingest_uploaded_document(project_id: str, document: DocumentInput) -> int:
    stored, _provider = _ingest_one_document(project_id, document)
    return stored


def _process_uploaded_repository(upload_id: str) -> None:
    upload = upload_manager.get(upload_id)
    try:
        project_store.set_index_status(upload.project_id, "indexing")
    except ProjectStoreError:
        pass
    upload_manager.process(upload_id, _ingest_uploaded_document)
    completed = upload_manager.get(upload_id)
    try:
        project_store.set_index_status(completed.project_id, completed.status)
    except ProjectStoreError:
        pass


@app.post(
    "/v1/documents/ingest-with-metadata",
    response_model=IngestResponse,
    tags=["Documents"],
)
def ingest_documents_with_project_metadata(
    payload: ProjectMetadataIngestRequest,
    request: Request,
) -> IngestResponse:
    metadata_records_stored = 0
    try:
        documents_registered = metadata_store.upsert_documents(
            payload.project_id,
            payload.documents,
        )
        if payload.metadata:
            metadata_store.upsert(
                MetadataUpsertRequest(
                    project_id=payload.project_id,
                    scope="project",
                    source="project-document-registration",
                    metadata=payload.metadata,
                )
            )
            metadata_records_stored = 1
    except MetadataStoreError as exc:
        raise _metadata_store_error() from exc
    _record_frontend_activity(
        request,
        payload.project_id,
        "documents.ingest-with-metadata",
    )
    return IngestResponse(
        project_id=payload.project_id,
        documents_received=len(payload.documents),
        chunks_stored=0,
        embedding_provider="not_requested",
        metadata_records_stored=metadata_records_stored,
        documents_registered=documents_registered,
    )


@app.post(
    "/v1/metadata",
    response_model=MetadataRecord,
    status_code=status.HTTP_201_CREATED,
)
def upsert_metadata(
    payload: MetadataUpsertRequest,
    request: Request,
) -> MetadataRecord:
    try:
        record = metadata_store.upsert(payload)
    except MetadataStoreError as exc:
        raise _metadata_store_error() from exc
    _record_frontend_activity(request, payload.project_id, "metadata.upsert")
    return record


@app.get(
    "/v1/projects/{project_id}/metadata",
    response_model=MetadataListResponse,
)
def list_project_metadata(
    project_id: str,
    scope: MetadataScope | None = None,
    limit: int = Query(default=5000, ge=1, le=10000),
) -> MetadataListResponse:
    normalized_project_id = project_id.strip()
    if not normalized_project_id:
        raise HTTPException(status_code=422, detail="project_id must not be blank")
    try:
        records = metadata_store.list_project(
            normalized_project_id,
            scope,
            limit,
        )
    except MetadataStoreError as exc:
        raise _metadata_store_error() from exc
    return MetadataListResponse(
        project_id=normalized_project_id,
        records=records,
    )


@app.post(
    "/v1/snapshots/compare",
    response_model=SnapshotCompareResponse,
    tags=["Projects"],
    summary="Compare a Frontend project identity with the Backend Snapshot baseline",
    description=(
        "Resolve project_id with commit_id or snapshot_id and return same, different, "
        "or unknown. A different result sets update_warning=true. Textual None/null "
        "values are normalized to an omitted optional identity."
    ),
    responses=ERROR_RESPONSES,
)
def compare_snapshot(
    payload: SnapshotCompareRequest,
    request: Request,
) -> SnapshotCompareResponse:
    started_at = perf_counter()
    request_id = _request_id(request)
    client_id = _frontend_client_id(request, payload.project_id)
    _safe_record_communication_event(
        request_id=request_id,
        channel="snapshot-control",
        direction="frontend_to_fastapi",
        phase="snapshot.compare.request",
        status="started",
        method="POST",
        path="/v1/snapshots/compare",
        client_id=client_id,
        project_id=payload.project_id,
        provider="snapshot-registry",
        details={
            "commit_id_supplied": payload.commit_id is not None,
            "snapshot_id_supplied": payload.snapshot_id is not None,
        },
    )
    try:
        result = snapshot_comparison_service.compare(
            payload,
            request_id=request_id,
        )
    except SnapshotComparisonError as exc:
        duration_ms = round((perf_counter() - started_at) * 1000)
        _safe_record_communication_event(
            request_id=request_id,
            channel="snapshot-control",
            direction="fastapi_to_frontend",
            phase="snapshot.compare.response",
            status="failed",
            method="POST",
            path="/v1/snapshots/compare",
            client_id=client_id,
            project_id=payload.project_id,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            duration_ms=duration_ms,
            provider="snapshot-registry",
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    duration_ms = round((perf_counter() - started_at) * 1000)
    _safe_record_communication_event(
        request_id=request_id,
        channel="snapshot-control",
        direction="fastapi_to_frontend",
        phase="snapshot.compare.response",
        status="success" if result.comparison == "same" else "warning",
        method="POST",
        path="/v1/snapshots/compare",
        client_id=client_id,
        project_id=payload.project_id,
        status_code=status.HTTP_200_OK,
        duration_ms=duration_ms,
        provider=result.baseline_source,
        model=result.comparison,
        details={
            "comparison": result.comparison,
            "same_version": result.same_version,
            "update_warning": result.update_warning,
            "registration_required": result.registration_required,
            "baseline_snapshot_id": result.baseline_snapshot_id,
            "baseline_commit_id": result.baseline_commit_id,
            "matched_snapshot_id": result.matched_snapshot_id,
            "reason_code": result.reason_code,
        },
    )
    return result


@app.post(
    "/v1/projects/{project_id}/version/check",
    response_model=ProjectVersionCheckResponse,
    tags=["Documents"],
    summary="Compare a local workspace version with the Backend DB registry",
    description=(
        "The VS Code extension sends the complete loaded workspace tree plus any "
        "snapshot, manifest hash, modified time, and Git information it has. "
        "Absolute client paths are ignored. The API compares the normalized tree "
        "with PROJECT_DB_LOCAL_ROOT/project_id and enriches it with PostgreSQL data."
    ),
)
def check_project_version(
    project_id: str,
    payload: ProjectVersionCheckRequest,
) -> ProjectVersionCheckResponse:
    normalized_project_id = project_id.strip()
    if not normalized_project_id:
        raise HTTPException(status_code=422, detail="project_id must not be blank")

    client_values = payload.model_dump(exclude={"tree"})
    if payload.tree is not None:
        try:
            structure_sha256, entry_count, tree_modified_at = (
                fingerprint_frontend_tree(payload.tree)
            )
        except LocalProjectError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        client_values["structure_sha256"] = structure_sha256
        client_values["entry_count"] = entry_count
        if client_values.get("modified_at") is None:
            client_values["modified_at"] = tree_modified_at
    client_version = ProjectVersionDescriptor(**client_values)

    postgres_version = None
    try:
        postgres_version = project_store.get_version(normalized_project_id)
    except ProjectStoreError:
        # The temporary local project registry remains usable without PostgreSQL.
        pass
    try:
        local_version = local_project_registry.get_version(normalized_project_id)
    except LocalProjectError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    if local_version is not None and postgres_version is not None:
        backend_version = dict(postgres_version)
        backend_version.update(
            {
                key: value
                for key, value in local_version.items()
                if value is not None
            }
        )
        backend_version["backend_source"] = "local+postgresql"
    elif local_version is not None:
        backend_version = local_version
    elif postgres_version is not None:
        backend_version = dict(postgres_version)
        backend_version["backend_source"] = "postgresql"
    else:
        backend_version = None

    return _version_check_result(
        normalized_project_id,
        client_version,
        backend_version,
    )


def _resolve_project_vector_runtime(project_id: str) -> dict[str, Any]:
    """Resolve the P2-I sole retrieval chain from ProjectVectorRoute.active_binding_id."""
    try:
        route = project_vector_route_store.get(project_id)
    except ProjectVectorRouteStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ProjectVectorRoute registry is unavailable",
        ) from exc
    if route is None or not route.active_binding_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"PROJECT_VECTOR_ROUTE_REQUIRED: project has no active retrieval binding: {project_id}",
        )
    try:
        candidate = project_vector_route_store.candidate_context(route.active_binding_id)
        if candidate is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Active SnapshotVectorBinding is missing: {route.active_binding_id}",
            )
        project_vector_route_store.validate_candidate(project_id, candidate)
        if not project_vector_route_store.current_runtime_routable(candidate):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="VECTOR_ROUTE_NOT_ROUTABLE: active external payload requires P3 hydration or inline content contract",
            )
        binding = snapshot_vector_binding_store.get(route.active_binding_id)
        if binding is None or binding.verification_state != "verified":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="SNAPSHOT_VECTOR_BINDING_REQUIRED: active route binding is not verified",
            )
        index = vector_index_store.get(binding.vector_index_id)
        if index is None or index.status != "ready":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="VECTOR_INDEX_UNAVAILABLE: active route VectorIndex is not ready",
            )
        snapshot = repository_store.get_snapshot(binding.snapshot_id)
        if snapshot is None or str(snapshot.get("project_id") or "") != project_id:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Active route Snapshot cannot be resolved for the project",
            )
        generation = (
            repository_store.get_generation(binding.generation_id)
            if binding.generation_id
            else None
        )
        target = vector_target_store.get(index.vector_target_id)
        profile = embedding_profile_store.get(index.embedding_profile_id)
        if index.ownership_mode == "external_attached":
            verification = external_vector_index_verification_store.get(index.vector_index_id)
            if verification is None or verification.verification_state != "compatible":
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="EXTERNAL_VECTOR_INDEX_NOT_COMPATIBLE: active external route lost compatibility",
                )
    except ProjectVectorRouteStoreError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except VectorIndexStoreError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="VectorIndex registry is unavailable") from exc
    except SnapshotVectorBindingStoreError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="SnapshotVectorBinding registry is unavailable") from exc
    except RepositoryStoreError as exc:
        raise _repository_store_error(exc) from exc
    except ExternalVectorIndexVerificationStoreError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="External VectorIndex verification registry is unavailable") from exc
    except VectorTargetStoreError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="VectorTarget registry is unavailable") from exc
    except EmbeddingProfileStoreError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="EmbeddingProfile registry is unavailable") from exc
    if target is None or profile is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VectorIndex target/profile reference cannot be resolved",
        )
    try:
        resolved_settings = settings_for_vector_index(
            settings.snapshot(), index=index, target=target, profile=profile
        )
        resolved_store = build_vector_store_for_index(
            resolved_settings, index=index, target=target, profile=profile
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"VECTOR_INDEX_UNAVAILABLE: {exc}",
        ) from exc
    return {
        "route": route,
        "candidate": candidate,
        "binding": binding,
        "snapshot": snapshot,
        "generation": generation,
        "index": index,
        "target": target,
        "profile": profile,
        "settings": resolved_settings,
        "embedding_service": EmbeddingService(resolved_settings),
        "vector_store": resolved_store,
    }

def _search_documents_with_runtime(
    payload: SearchRequest,
    runtime: dict[str, Any],
) -> SearchResponse:
    resolved_embedding_service: EmbeddingService = runtime["embedding_service"]
    resolved_vector_store = runtime["vector_store"]
    index: VectorIndexRecord = runtime["index"]
    generation = runtime["generation"]
    binding: SnapshotVectorBindingRecord = runtime["binding"]
    profile: EmbeddingProfileRecord = runtime["profile"]
    try:
        embedding = resolved_embedding_service.embed(payload.query, input_type="query")
        results = resolved_vector_store.search(
            payload.project_id,
            embedding.vector,
            embedding.provider,
            embedding.model,
            payload.top_k,
            (str(generation["generation_id"]) if generation is not None else None),
        )
        results = project_store.enrich_sources(results)
    except (ServiceError, VectorStoreError) as exc:
        raise _service_error(exc) from exc
    except ProjectStoreError as exc:
        raise _project_store_error() from exc
    return SearchResponse(
        project_id=payload.project_id,
        query=payload.query,
        results=results,
        embedding_provider=embedding.provider,
        embedding_profile_id=profile.embedding_profile_id,
        vector_index_id=index.vector_index_id,
        snapshot_id=binding.snapshot_id,
        generation_id=binding.generation_id,
        snapshot_vector_binding_id=binding.binding_id,
        vector_route_revision=runtime["route"].revision,
    )


@app.post("/v1/search", response_model=SearchResponse, tags=["Documents"])
def search_documents(payload: SearchRequest) -> SearchResponse:
    runtime = _resolve_project_vector_runtime(payload.project_id)
    return _search_documents_with_runtime(payload, runtime)


@app.get(
    "/v1/contracts/canonical-context",
    tags=["Chat"],
    summary="Read the frozen P3 Canonical Context JSON Schema",
)
def canonical_context_contract() -> dict[str, Any]:
    """Expose the machine-readable contract without exposing request evidence."""

    return {
        "schema_version": CANONICAL_CONTEXT_SCHEMA_VERSION,
        "target_retrieval_path": "/prompt",
        "target_retrieval_owner": "vectordb",
        "target_prompt_owner": "vectordb",
        "hydration_owner": "vectordb",
        "raw_content_retention": "request_scoped",
        "schema": CanonicalContext.model_json_schema(),
    }


@app.get(
    "/v1/contracts/vector-service",
    tags=["Documents"],
    summary="Read the external VectorDB conformance contract",
)
def external_vector_service_contract() -> dict[str, Any]:
    return vector_service_contract()


def _chat_context_response(record: ChatContextRecord) -> ChatContextResponse:
    return ChatContextResponse(
        context_id=record.context_id,
        project_id=record.project_id,
        commit_id=record.commit_id,
        snapshot_id=record.snapshot_id,
        resolution=record.resolution,
        grounding_available=record.grounding_available,
        created_at=record.created_at,
        expires_at=record.expires_at,
    )


@app.post(
    "/v1/chat/contexts",
    response_model=ChatContextResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Chat"],
    summary="Register optional project and Snapshot context separately from Chat",
)
def register_chat_context(
    payload: ChatContextRegistrationRequest,
    request: Request,
) -> ChatContextResponse:
    try:
        record = chat_context_service.register(
            owner_client_id=_chat_owner_id(request),
            project_id=payload.project_id,
            commit_id=payload.commit_id,
            snapshot_id=payload.snapshot_id,
        )
    except ChatContextError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return _chat_context_response(record)


@app.get(
    "/v1/chat/contexts/{context_id}",
    response_model=ChatContextResponse,
    tags=["Chat"],
    summary="Read an unexpired Chat Context owned by the current Client",
)
def get_chat_context(context_id: str, request: Request) -> ChatContextResponse:
    try:
        record = chat_context_service.get(
            context_id,
            owner_client_id=_chat_owner_id(request),
        )
    except ChatContextError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return _chat_context_response(record)


@app.get(
    "/v1/contracts/chat-stream",
    tags=["Chat"],
    summary="Read the P3-C Chat SSE event contract",
)
def chat_stream_contract() -> dict[str, Any]:
    return {
        "schema_version": API_SCHEMA_VERSION,
        "transport": "server-sent-events",
        "media_type": "text/event-stream",
        "events": ["meta", "status", "delta", "done", "error"],
        "display_states": {
            "meta": "전송중",
            "status": "추론중",
            "delta": "생각중",
            "done": "답변중",
            "error": "답변 실패",
        },
        "delta_semantics": "answer_text_only",
        "chain_of_thought_exposed": False,
        "context_header": "X-Vision-Context-ID",
        "negotiation": {
            "preferred": "Accept: text/event-stream",
            "compatibility": "stream=true",
            "json": "Accept: application/json or explicit stream=false",
        },
    }


def _cache_chat_response(request: Request, response: ChatResponse) -> None:
    request_id = str(response.metadata.get("request_id") or "").strip()
    if not request_id:
        return
    try:
        redis_coordinator.set_ephemeral_json(
            "chat-result",
            request_id,
            {
                "owner_client_id": _chat_owner_id(request),
                "response": response.model_dump(mode="json"),
            },
            ttl_seconds=3_600,
        )
    except (DistributedStateError, TypeError, ValueError) as exc:
        # Citation replay is optional and must never make Chat fail.
        logger.warning("Chat result cache unavailable request_id=%s: %s", request_id, exc)


@app.get(
    "/v1/citations/{request_id}/{citation_id}",
    tags=["Chat"],
    summary="Read one short-lived citation from a completed Chat response",
)
def get_chat_citation(
    request_id: str,
    citation_id: int,
    request: Request,
) -> dict[str, Any]:
    if citation_id < 1:
        raise HTTPException(status_code=422, detail="citation_id는 1 이상이어야 합니다.")
    try:
        cached = redis_coordinator.get_ephemeral_json("chat-result", request_id)
    except DistributedStateError as exc:
        raise HTTPException(status_code=503, detail="Citation 저장소를 사용할 수 없습니다.") from exc
    if cached is None:
        raise HTTPException(status_code=404, detail="Citation이 없거나 만료되었습니다.")
    if cached.get("owner_client_id") != _chat_owner_id(request):
        raise HTTPException(status_code=403, detail="다른 Frontend Client의 Citation입니다.")
    response = cached.get("response")
    sources = response.get("source") if isinstance(response, dict) else None
    if not isinstance(sources, list) or citation_id > len(sources):
        raise HTTPException(status_code=404, detail="citation_id를 찾을 수 없습니다.")
    return {
        "schema_version": API_SCHEMA_VERSION,
        "request_id": request_id,
        "citation_id": citation_id,
        "source": sources[citation_id - 1],
    }


def _sse_event(
    event: str,
    request_id: str,
    sequence: int,
    values: dict[str, Any] | None = None,
    *,
    simulated: bool | None = None,
    progress_source: str | None = None,
) -> str:
    progress = simulated_chat_progress(request_id, event)  # type: ignore[arg-type]
    if simulated is not None:
        progress["simulated"] = simulated
    if progress_source is not None:
        progress["progress_source"] = progress_source
    payload = {**progress, "sequence": sequence, **(values or {})}
    return (
        f"id: {sequence}\n"
        f"event: {event}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


def _external_prompt_chat(
    *,
    payload: ChatRequest,
    request: Request,
    request_id: str,
    effective_project_id: str,
    requested_model_id: str,
    processing_mode: str,
    project_resolution: Any,
    intake_result: Any,
    context_chars: int,
    overall_started: float,
    delta_callback: Callable[[str], None] | None = None,
) -> ChatResponse:
    """P3-B: VectorDB owns retrieval, hydration and prompt construction."""

    try:
        snapshot = (
            repository_store.get_snapshot(payload.snapshot_id)
            if payload.snapshot_id
            else repository_store.get_current_snapshot_context(effective_project_id)
        )
    except RepositoryStoreError as exc:
        raise _repository_store_error(exc) from exc
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "EXTERNAL_VECTOR_SNAPSHOT_REQUIRED: project has no current Snapshot"
            ),
        )
    if str(snapshot.get("project_id") or "") != effective_project_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="요청 Snapshot이 project_id에 속하지 않습니다.",
        )
    snapshot_id = str(snapshot["snapshot_id"])
    revision = str(snapshot.get("revision") or "").strip() or None
    if payload.snapshot_id and payload.snapshot_id != snapshot_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "요청 snapshot_id가 현재 project Snapshot과 일치하지 않습니다. "
                f"requested={payload.snapshot_id!r}, active={snapshot_id!r}."
            ),
        )
    telemetry_client_id = _frontend_client_id(request, effective_project_id)
    _safe_record_chat_audit_request(
        request_id=request_id,
        client_id=telemetry_client_id,
        project_id=effective_project_id,
        session_id=payload.session_id,
        requested_model_id=requested_model_id,
        message=payload.message,
        history_count=len(payload.history),
        context_chars=context_chars,
    )
    _record_frontend_activity(request, effective_project_id, "chat.request")
    retrieval_started = perf_counter()
    _safe_record_communication_event(
        request_id=request_id,
        channel="rag",
        direction="fastapi_to_vectordb",
        phase="rag.prompt.request",
        status="started",
        client_id=telemetry_client_id,
        project_id=effective_project_id,
        provider="rag_lab",
        details={
            "endpoint": f"{rag_lab_client.base_url}/prompt",
            "snapshot_id": snapshot_id,
            "revision": revision,
        },
    )
    try:
        external_binding = rag_lab_client.resolve_project(
            effective_project_id,
            revision=revision,
        )
        prompt_result = rag_lab_client.prompt(
            external_binding.external_project_id,
            payload.message,
        )
    except RagLabError as exc:
        retrieval_ms = round((perf_counter() - retrieval_started) * 1000)
        _safe_record_communication_event(
            request_id=request_id,
            channel="rag",
            direction="vectordb_to_fastapi",
            phase="rag.prompt.response",
            status="error",
            client_id=telemetry_client_id,
            project_id=effective_project_id,
            status_code=exc.status_code,
            duration_ms=retrieval_ms,
            provider="rag_lab",
            error=str(exc),
        )
        _safe_complete_chat_audit(
            request_id=request_id,
            status="error",
            status_code=exc.status_code,
            used_model_id=requested_model_id,
            source_count=0,
            duration_ms=round((perf_counter() - overall_started) * 1000),
            error=str(exc),
        )
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    retrieval_ms = round((perf_counter() - retrieval_started) * 1000)
    canonical_context = build_canonical_context(
        request_id=request_id,
        client_id=getattr(request.state, "frontend_client_id", None),
        project_id=effective_project_id,
        snapshot_id=snapshot_id,
        session_id=payload.session_id,
        query=payload.message,
        retrieval=CanonicalContextRetrieval(
            owner="vectordb",
            mode="prompt",
            prompt_owner="vectordb",
            provider="rag_lab",
            endpoint=f"{rag_lab_client.base_url}/prompt",
            has_evidence=prompt_result.has_evidence,
            reason=prompt_result.reason,
            top_score=prompt_result.top_score,
            threshold=prompt_result.threshold,
        ),
        messages=prompt_result.messages,
        sources=prompt_result.sources,
        provenance={
            "external_project_id": external_binding.external_project_id,
            "binding_strength": external_binding.binding_strength,
            "verification_state": external_binding.verification_state,
            "revision": external_binding.revision,
            "indexed_at": external_binding.indexed_at,
            "fingerprint": external_binding.fingerprint,
            "manifest_sha256": snapshot.get("manifest_sha256"),
            **prompt_result.provenance,
        },
    )
    _safe_record_communication_event(
        request_id=request_id,
        channel="rag",
        direction="vectordb_to_fastapi",
        phase="rag.prompt.response",
        status="success",
        client_id=telemetry_client_id,
        project_id=effective_project_id,
        status_code=200,
        duration_ms=retrieval_ms,
        provider="rag_lab",
        source_count=len(canonical_context.sources),
        details={
            "canonical_context_id": canonical_context.context_id,
            "external_project_id": external_binding.external_project_id,
            "binding_strength": external_binding.binding_strength,
            "verification_state": external_binding.verification_state,
            "has_evidence": prompt_result.has_evidence,
        },
    )

    generation_started = perf_counter()
    if prompt_result.has_evidence:
        try:
            generation = generation_router.generate(
                payload.model_id,
                payload.message,
                prompt_result.sources,
                payload.history,
                payload.context,
                effective_project_id,
                payload.session_id,
                request_id=request_id,
                prompt_mode="external_vector_prompt",
                messages_override=prompt_result.messages,
                delta_callback=delta_callback,
                routing_metadata={
                    "request_id": request_id,
                    "client_id": getattr(request.state, "frontend_client_id", None),
                    "project_id": effective_project_id,
                    "snapshot_id": snapshot_id,
                    "session_id": payload.session_id,
                    "context_id": canonical_context.context_id,
                },
            )
        except ServiceError as exc:
            _safe_complete_chat_audit(
                request_id=request_id,
                status="error",
                status_code=getattr(exc, "status_code", 503),
                used_model_id=requested_model_id,
                source_count=len(prompt_result.sources),
                duration_ms=round((perf_counter() - overall_started) * 1000),
                error=str(exc),
            )
            raise _service_error(exc) from exc
        answer = generation.answer
        used_model_id = generation.used_model_id
        used_model_name = generation.used_model_name
        provider = generation.provider
        generated_request_id = generation.request_id
    else:
        answer = "NO_EVIDENCE"
        used_model_id = requested_model_id
        used_model_name = requested_model_id
        provider = "not_called"
        generated_request_id = request_id
    generation_ms = round((perf_counter() - generation_started) * 1000)
    total_ms = round((perf_counter() - overall_started) * 1000)
    # VectorDB may return below-threshold candidates while has_evidence=false.
    # They are retrieval diagnostics, not answer citations, and must not be
    # exposed beside NO_EVIDENCE in the public Chat response.
    chat_sources = (
        [
            source.model_copy(update={"citation_id": index})
            for index, source in enumerate(prompt_result.sources, start=1)
        ]
        if prompt_result.has_evidence
        else []
    )
    _safe_complete_chat_audit(
        request_id=request_id,
        status="completed",
        status_code=200,
        answer=answer,
        used_model_id=used_model_id,
        provider=provider,
        source_count=len(chat_sources),
        duration_ms=total_ms,
    )
    return ChatResponse(
        answer=answer,
        source=[
            SourceDocument(
                file=source.path or source.document_id,
                chunk=(
                    source.text
                    or str(source.metadata.get("section") or "")
                    or str(source.metadata.get("external_source_id") or source.chunk_id)
                ),
                score=source.score,
            )
            for source in chat_sources
        ],
        metadata={
            "schema_version": API_SCHEMA_VERSION,
            "request_id": generated_request_id,
            "client_request_id": payload.client_request_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "completed",
            "project_id": effective_project_id,
            "requested_project_id": payload.project_id,
            "resolved_project_id": effective_project_id,
            "project_resolution": project_resolution.metadata(),
            "session_id": payload.session_id,
            "requested_model_id": requested_model_id,
            "used_model_id": used_model_id,
            "provider": provider,
            "ai_model": used_model_name,
            "chat_processing_mode": processing_mode,
            "chat_route": "external_vector_prompt",
            "prompt_owner": "vectordb",
            "snapshot_id": snapshot_id,
            "requested_snapshot_id": payload.snapshot_id,
            "external_vector": {
                "base_url": rag_lab_client.base_url,
                "project_id": external_binding.external_project_id,
                "binding_strength": external_binding.binding_strength,
                "verification_state": external_binding.verification_state,
                "revision": external_binding.revision,
                "indexed_at": external_binding.indexed_at,
            },
            "canonical_context": {
                "schema_version": canonical_context.schema_version,
                "context_id": canonical_context.context_id,
                "retrieval_owner": "vectordb",
                "retrieval_mode": "prompt",
                "prompt_owner": "vectordb",
                "raw_content_retention": canonical_context.retention.policy,
                "source_count": len(canonical_context.sources),
            },
            "timing": {
                "retrieval_ms": retrieval_ms,
                "generation_ms": generation_ms,
                "total_ms": total_ms,
            },
            "request_normalization": intake_result.metadata(debug=payload.debug),
            "source_count": len(chat_sources),
        },
    )

def _chat_json(
    payload: ChatRequest,
    request: Request,
    *,
    delta_callback: Callable[[str], None] | None = None,
) -> ChatResponse:
    # project_id is the canonical Backend registry key. session_id identifies
    # one client conversation and is independent from the retrieval scope.
    overall_started = perf_counter()
    request_id = _request_id(request)
    try:
        global_intake_settings = chat_intake_settings_store.get()
        deep_normalization_enabled = resolve_deep_normalization(
            global_intake_settings.deep_normalization_enabled,
            getattr(
                request.state,
                "chat_deep_normalization_mode",
                "inherit",
            ),
        )
    except ChatIntakeSettingsError as exc:
        # Basic schema normalization is still active. Deep interpretation is an
        # optional convenience and must not make Chat unavailable with stale DB state.
        logger.warning("Chat deep-normalization settings unavailable: %s", exc)
        deep_normalization_enabled = False
    intake_result = normalize_chat_intake(
        payload,
        deep_enabled=deep_normalization_enabled,
    )
    payload = intake_result.payload
    requested_model_id = payload.model_id or generation_router.default_model_id
    processing_mode = generation_router.chat_processing_mode(requested_model_id)
    route_decision = (
        classify_chat_request(payload)
        if processing_mode == "vision_managed"
        else None
    )
    project_resolution = None
    project_registry_error: str | None = None
    if route_decision is not None and route_decision.project_required:
        try:
            project_resolution = resolve_project_id(
                payload.project_id,
                project_store.list_projects(),
                configured_aliases=settings.project_id_aliases,
            )
        except ProjectStoreError as exc:
            # Snapshot-grounded requests must remain fail-closed. A plain
            # workspace/project hint must not make ordinary Chat unavailable.
            if payload.snapshot_id:
                raise _project_store_error() from exc
            project_registry_error = type(exc).__name__
    unresolved_project_fallback = bool(
        route_decision is not None
        and allows_unresolved_project_fallback(
            route_decision,
            resolved_project_id=(
                project_resolution.resolved_project_id
                if project_resolution is not None
                else None
            ),
            snapshot_id=payload.snapshot_id,
        )
    )
    context_chars = (
        len(payload.context)
        if isinstance(payload.context, str)
        else len(
            json.dumps(
                [item.model_dump(mode="json") for item in payload.context],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    )
    usable_frontend_context = _has_usable_frontend_context(payload.context)
    direct_generation = (
        processing_mode == "provider_managed"
        or (route_decision is not None and not route_decision.project_required)
        or unresolved_project_fallback
    )

    if direct_generation:
        effective_project_id = (
            payload.project_id
            if (
                not unresolved_project_fallback
                and payload.project_id not in {"__auto__", "auto", "default"}
            )
            else "__unscoped__"
        )
        if unresolved_project_fallback:
            project_resolution_metadata = (
                project_resolution.metadata()
                if project_resolution is not None
                else {
                    "requested_project_id": payload.project_id,
                    "resolved_project_id": None,
                    "strategy": "project_registry_unavailable",
                    "confidence": 0.0,
                    "candidates": [],
                    "error": project_registry_error,
                }
            )
        else:
            project_resolution_metadata = {
                "requested_project_id": payload.project_id,
                "resolved_project_id": (
                    None
                    if effective_project_id == "__unscoped__"
                    else effective_project_id
                ),
                "strategy": "not_required",
                "confidence": 1.0,
                "candidates": [],
            }
        telemetry_client_id = _frontend_client_id(request, effective_project_id)
        _safe_record_chat_audit_request(
            request_id=request_id,
            client_id=telemetry_client_id,
            project_id=effective_project_id,
            session_id=payload.session_id,
            requested_model_id=requested_model_id,
            message=payload.message,
            history_count=len(payload.history),
            context_chars=context_chars,
        )
        _record_frontend_activity(request, effective_project_id, "chat.request")
        generation_started = perf_counter()
        _safe_record_communication_event(
            request_id=request_id, channel="fastapi-ai",
            direction="fastapi_to_ai_server", phase="ai.request", status="started",
            client_id=telemetry_client_id, project_id=effective_project_id,
            provider="model-router", model=requested_model_id, source_count=0,
            details={
                "chat_processing_mode": processing_mode,
                "chat_route": "provider_managed" if processing_mode == "provider_managed" else "general",
                "rag_sources_attached": 0,
                "frontend_context_available": usable_frontend_context,
                "request_normalization": intake_result.metadata(debug=payload.debug),
                "snapshot_id": payload.snapshot_id,
            },
        )
        try:
            generation = generation_router.generate(
                payload.model_id, payload.message, [], payload.history, payload.context,
                effective_project_id, payload.session_id, request_id=request_id,
                prompt_mode=(
                    "provider_managed" if processing_mode == "provider_managed" else "direct"
                ),
                routing_metadata={
                    "request_id": request_id,
                    "client_id": getattr(request.state, "frontend_client_id", None),
                    "project_id": (
                        None if effective_project_id == "__unscoped__" else effective_project_id
                    ),
                    "snapshot_id": payload.snapshot_id,
                    "session_id": payload.session_id,
                },
                delta_callback=delta_callback,
            )
        except ServiceError as exc:
            _safe_complete_chat_audit(
                request_id=request_id, status="error",
                status_code=getattr(exc, "status_code", 503),
                used_model_id=requested_model_id, source_count=0,
                duration_ms=round((perf_counter() - overall_started) * 1000),
                error=str(exc),
            )
            raise _service_error(exc) from exc
        generation_ms = round((perf_counter() - generation_started) * 1000)
        total_ms = round((perf_counter() - overall_started) * 1000)
        _safe_record_communication_event(
            request_id=request_id, channel="fastapi-ai",
            direction="ai_server_to_fastapi", phase="ai.response", status="success",
            client_id=telemetry_client_id, project_id=effective_project_id,
            status_code=200, duration_ms=generation_ms, provider=generation.provider,
            model=generation.used_model_name, source_count=0,
            details={
                "answer_chars": len(generation.answer),
                "chat_processing_mode": processing_mode,
                "rag_sources_attached": 0,
            },
        )
        _safe_complete_chat_audit(
            request_id=request_id, status="completed", status_code=200,
            answer=generation.answer, used_model_id=generation.used_model_id,
            provider=generation.provider, source_count=0, duration_ms=total_ms,
        )
        route_name = "provider_managed" if processing_mode == "provider_managed" else "general"
        return ChatResponse(
            answer=generation.answer,
            source=[],
            metadata={
                "schema_version": API_SCHEMA_VERSION,
                "request_id": generation.request_id,
                "client_request_id": payload.client_request_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "completed",
                "project_id": None if effective_project_id == "__unscoped__" else effective_project_id,
                "requested_project_id": payload.project_id,
                "resolved_project_id": None if effective_project_id == "__unscoped__" else effective_project_id,
                "project_resolution": project_resolution_metadata,
                "session_id": payload.session_id,
                "requested_model_id": generation.requested_model_id,
                "used_model_id": generation.used_model_id,
                "provider": generation.provider,
                "chat_processing_mode": processing_mode,
                "chat_route": route_name,
                "chat_route_reasons": [
                    *(list(route_decision.reasons) if route_decision else []),
                    *(
                        ["unresolved_project_hint_fallback"]
                        if unresolved_project_fallback
                        else []
                    ),
                ],
                "fallback_used": generation.requested_model_id != generation.used_model_id,
                "finish_reason": "stop",
                "timing": {"retrieval_ms": 0, "generation_ms": generation_ms, "total_ms": total_ms},
                "retrieval": {
                    "skipped": True,
                    "reason": (
                        "provider_managed"
                        if processing_mode == "provider_managed"
                        else "unresolved_project_hint"
                        if unresolved_project_fallback
                        else "general_chat"
                    ),
                },
                "request_normalization": {
                    "auto_project_requested": payload.project_id == "__auto__",
                    "fallback_session_id": payload.session_id.startswith("vscode-"),
                    "accepted_extra_fields": sorted((payload.model_extra or {}).keys()),
                    **intake_result.metadata(debug=payload.debug),
                },
                "snapshot_id": payload.snapshot_id,
                "history_messages": len(payload.history),
                "source_count": 0,
                "sources": [],
            },
        )

    # Strict project-grounded requests reach this point only with a resolved
    # project, or with an explicit Snapshot that must fail closed below.
    if project_resolution is None:
        raise _project_store_error()
    if project_resolution.resolved_project_id is None:
        candidate_text = (
            ", ".join(project_resolution.candidates)
            if project_resolution.candidates
            else "none"
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "project_id를 인덱싱된 프로젝트로 결정할 수 없습니다. "
                f"requested={payload.project_id!r}, "
                f"candidates={candidate_text}. "
                "Frontend가 project_id를 확정할 필요는 없습니다. workspace/reference 같은 "
                "프로젝트 힌트를 전달하거나 GET /v1/IngestResponse에서 상태를 확인하세요."
            ),
        )
    effective_project_id = project_resolution.resolved_project_id
    if settings.rag_lab_base_url:
        return _external_prompt_chat(
            payload=payload,
            request=request,
            request_id=request_id,
            effective_project_id=effective_project_id,
            requested_model_id=requested_model_id,
            processing_mode=processing_mode,
            project_resolution=project_resolution,
            intake_result=intake_result,
            context_chars=context_chars,
            overall_started=overall_started,
            delta_callback=delta_callback,
        )
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            "VECTOR_PROMPT_PROVIDER_REQUIRED: project-grounded Chat requires "
            "a VectorDB /prompt provider because Vision-owned AI prompts are disabled"
        ),
    )
    retrieval_runtime = _resolve_project_vector_runtime(effective_project_id)
    retrieval_index: VectorIndexRecord = retrieval_runtime["index"]
    retrieval_binding: SnapshotVectorBindingRecord = retrieval_runtime["binding"]
    retrieval_profile: EmbeddingProfileRecord = retrieval_runtime["profile"]
    retrieval_target: VectorTargetRecord = retrieval_runtime["target"]
    if (
        payload.snapshot_id
        and payload.snapshot_id != retrieval_binding.snapshot_id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "요청 snapshot_id가 현재 project retrieval route와 일치하지 않습니다. "
                f"requested={payload.snapshot_id!r}, "
                f"active={retrieval_binding.snapshot_id!r}."
            ),
        )
    logger.info(
        "Resolved chat project requested=%r resolved=%r strategy=%s confidence=%.4f",
        payload.project_id, effective_project_id, project_resolution.strategy,
        project_resolution.confidence,
    )
    candidate_k = settings.rag_candidate_k
    reasoning_mode = payload.reasoning_mode or settings.agentic_rag_default_mode
    telemetry_client_id = _frontend_client_id(request, effective_project_id)
    _safe_record_chat_audit_request(
        request_id=request_id, client_id=telemetry_client_id,
        project_id=effective_project_id, session_id=payload.session_id,
        requested_model_id=requested_model_id, message=payload.message,
        history_count=len(payload.history), context_chars=context_chars,
    )
    _record_frontend_activity(request, effective_project_id, "chat.request")
    query_plan = agentic_query_planner.plan(
        payload.message,
        payload.history,
        reasoning_mode,
        payload.context,
    )
    resolved_reasoning_mode = query_plan.mode
    retrieval_started = perf_counter()
    _safe_record_communication_event(
        request_id=request_id,
        channel="rag",
        direction="fastapi_to_vectordb",
        phase="rag.request",
        status="started",
        client_id=telemetry_client_id,
        project_id=effective_project_id,
        provider=retrieval_target.engine,
        model=retrieval_profile.model,
        details={
            "policy": f"agentic-rag-v1/{resolved_reasoning_mode}",
            "requested_reasoning_mode": reasoning_mode,
            "reasoning_mode": resolved_reasoning_mode,
            "candidate_k": candidate_k,
            "frontend_context_available": usable_frontend_context,
            "project_resolution": project_resolution.metadata(),
            "request_normalization": intake_result.metadata(debug=payload.debug),
            "requested_snapshot_id": payload.snapshot_id,
        },
    )
    retrieval_degraded_error: str | None = None
    retrieval_degraded_status_code: int | None = None
    try:
        agentic_result = agentic_rag.run(
            query_plan,
            lambda query: _search_documents_with_runtime(
                SearchRequest(
                    project_id=effective_project_id,
                    query=query,
                    top_k=candidate_k,
                ),
                retrieval_runtime,
            ),
        )
        retrieval_decision = agentic_result.decision
        agentic_trace = agentic_result.trace
        embedding_provider = agentic_result.embedding_provider
        selected_sources = retrieval_decision.sources
    except HTTPException as exc:
        if exc.status_code >= 500 and usable_frontend_context:
            retrieval_degraded_error = str(exc.detail)
            retrieval_degraded_status_code = exc.status_code
            agentic_result = agentic_rag.fallback(
                query_plan,
                stop_reason="vector_unavailable_context_fallback",
            )
            retrieval_decision = agentic_result.decision
            agentic_trace = agentic_result.trace
            embedding_provider = agentic_result.embedding_provider
            selected_sources = retrieval_decision.sources
        else:
            retrieval_ms = round((perf_counter() - retrieval_started) * 1000)
            _safe_record_communication_event(
                request_id=request_id,
                channel="rag",
                direction="vectordb_to_fastapi",
                phase="rag.response",
                status="error",
                client_id=telemetry_client_id,
                project_id=effective_project_id,
                status_code=exc.status_code,
                duration_ms=retrieval_ms,
                provider=retrieval_target.engine,
                model=retrieval_profile.model,
                error=str(exc),
                details={
                    "policy": f"agentic-rag-v1/{resolved_reasoning_mode}",
                    "requested_reasoning_mode": reasoning_mode,
                    "reasoning_mode": resolved_reasoning_mode,
                    "candidate_k": candidate_k,
                    "frontend_context_available": usable_frontend_context,
                    "project_resolution": project_resolution.metadata(),
                },
            )
            _safe_complete_chat_audit(
                request_id=request_id,
                status="error",
                status_code=exc.status_code,
                used_model_id=requested_model_id,
                source_count=0,
                duration_ms=round((perf_counter() - overall_started) * 1000),
                error=str(exc.detail),
            )
            raise
    retrieval_ms = round((perf_counter() - retrieval_started) * 1000)
    _safe_record_communication_event(
        request_id=request_id,
        channel="rag",
        direction="vectordb_to_fastapi",
        phase="rag.response",
        status=("degraded" if retrieval_degraded_error else "success"),
        client_id=telemetry_client_id,
        project_id=effective_project_id,
        status_code=retrieval_degraded_status_code or 200,
        duration_ms=retrieval_ms,
        provider=embedding_provider,
        model=retrieval_profile.model,
        source_count=len(selected_sources),
        error=retrieval_degraded_error,
        details={
            "policy": retrieval_decision.policy,
            "candidate_k": candidate_k,
            "candidate_count": retrieval_decision.candidate_count,
            "reranked_count": retrieval_decision.reranked_count,
            "selected_count": retrieval_decision.selected_count,
            "context_chars": retrieval_decision.context_chars,
            "requested_reasoning_mode": agentic_trace.requested_mode,
            "reasoning_mode": agentic_trace.mode,
            "step_count": agentic_trace.step_count,
            "max_steps": agentic_trace.max_steps,
            "follow_up_rewritten": agentic_trace.follow_up_rewritten,
            "context_grounded": agentic_trace.context_grounded,
            "evidence_coverage": agentic_trace.evidence_coverage,
            "final_novelty_ratio": agentic_trace.final_novelty_ratio,
            "stop_reason": agentic_trace.stop_reason,
            "degraded": retrieval_degraded_error is not None,
            "frontend_context_available": usable_frontend_context,
            "client_top_k_ignored": payload.top_k is not None,
            "project_resolution": project_resolution.metadata(),
        },
    )
    canonical_context = build_canonical_context(
        request_id=request_id,
        client_id=getattr(request.state, "frontend_client_id", None),
        project_id=effective_project_id,
        snapshot_id=retrieval_binding.snapshot_id,
        session_id=payload.session_id,
        query=query_plan.standalone_query,
        retrieval=CanonicalContextRetrieval(
            owner="vision_legacy",
            mode="search",
            prompt_owner="vision_legacy",
            provider=retrieval_target.engine,
            endpoint=retrieval_target.endpoint,
            has_evidence=bool(selected_sources),
            reason=(
                retrieval_degraded_error
                or agentic_trace.stop_reason
                or ("ok" if selected_sources else "no_evidence")
            ),
            top_score=(
                max(source.score for source in selected_sources)
                if selected_sources
                else None
            ),
            threshold=settings.rag_min_score,
        ),
        sources=selected_sources,
        provenance={
            "vector_index_id": retrieval_index.vector_index_id,
            "snapshot_vector_binding_id": retrieval_binding.binding_id,
            "generation_id": retrieval_binding.generation_id,
            "vector_route_revision": retrieval_runtime["route"].revision,
            "transition_state": "p2_legacy_runtime",
        },
    )
    generation_started = perf_counter()
    _safe_record_communication_event(
        request_id=request_id,
        channel="fastapi-ai",
        direction="fastapi_to_ai_server",
        phase="ai.request",
        status="started",
        client_id=telemetry_client_id,
        project_id=effective_project_id,
        provider="model-router",
        model=requested_model_id,
        source_count=len(selected_sources),
        details={
            "rag_sources_attached": len(selected_sources),
            "rag_degraded": retrieval_degraded_error is not None,
            "frontend_context_available": usable_frontend_context,
            "canonical_context_id": canonical_context.context_id,
        },
    )
    try:
        generation = generation_router.generate(
            payload.model_id,
            payload.message,
            selected_sources,
            payload.history,
            payload.context,
            effective_project_id,
            payload.session_id,
            request_id=request_id,
            prompt_mode="passthrough",
            routing_metadata={
                "request_id": request_id,
                "client_id": getattr(request.state, "frontend_client_id", None),
                "project_id": effective_project_id,
                "snapshot_id": retrieval_binding.snapshot_id,
                "session_id": payload.session_id,
                "context_id": canonical_context.context_id,
            },
            delta_callback=delta_callback,
        )
    except ServiceError as exc:
        generation_ms = round((perf_counter() - generation_started) * 1000)
        _safe_record_communication_event(
            request_id=request_id,
            channel="fastapi-ai",
            direction="ai_server_to_fastapi",
            phase="ai.response",
            status="error",
            client_id=telemetry_client_id,
            project_id=effective_project_id,
            status_code=getattr(exc, "status_code", 503),
            duration_ms=generation_ms,
            provider="model-router",
            model=requested_model_id,
            source_count=len(selected_sources),
            error=str(exc),
            details={"rag_sources_attached": len(selected_sources)},
        )
        _safe_complete_chat_audit(
            request_id=request_id,
            status="error",
            status_code=getattr(exc, "status_code", 503),
            used_model_id=requested_model_id,
            source_count=len(selected_sources),
            duration_ms=round((perf_counter() - overall_started) * 1000),
            error=str(exc),
        )
        raise _service_error(exc) from exc
    generation_ms = round((perf_counter() - generation_started) * 1000)
    _safe_record_communication_event(
        request_id=request_id,
        channel="fastapi-ai",
        direction="ai_server_to_fastapi",
        phase="ai.response",
        status="success",
        client_id=telemetry_client_id,
        project_id=effective_project_id,
        status_code=200,
        duration_ms=generation_ms,
        provider=generation.provider,
        model=generation.used_model_name,
        source_count=len(selected_sources),
        details={
            "answer_chars": len(generation.answer),
            "rag_sources_attached": len(selected_sources),
        },
    )
    total_ms = round((perf_counter() - overall_started) * 1000)
    chat_sources = [
        source.model_copy(update={"citation_id": index})
        for index, source in enumerate(selected_sources, start=1)
    ]
    timing = {
        "retrieval_ms": retrieval_ms,
        "generation_ms": generation_ms,
        "total_ms": total_ms,
    }
    _safe_complete_chat_audit(
        request_id=request_id,
        status="completed",
        status_code=200,
        answer=generation.answer,
        used_model_id=generation.used_model_id,
        provider=generation.provider,
        source_count=len(chat_sources),
        duration_ms=total_ms,
    )
    return ChatResponse(
        answer=generation.answer,
        source=[
            SourceDocument(
                file=source.path or source.document_id,
                chunk=source.text,
                score=source.score,
            )
            for source in chat_sources
        ],
        metadata={
            "schema_version": API_SCHEMA_VERSION,
            "request_id": generation.request_id,
            "client_request_id": payload.client_request_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "completed",
            "project_id": effective_project_id,
            "requested_project_id": payload.project_id,
            "resolved_project_id": effective_project_id,
            "project_resolution": project_resolution.metadata(),
            "session_id": payload.session_id,
            "client": {
                "client_id": getattr(
                    request.state,
                    "frontend_client_id",
                    None,
                ),
                "auto_registered": bool(
                    getattr(
                        request.state,
                        "frontend_client_auto_registered",
                        False,
                    )
                ),
            },
            "requested_model_id": generation.requested_model_id,
            "used_model_id": generation.used_model_id,
            "provider": generation.provider,
            "chat_processing_mode": processing_mode,
            "chat_route": "project_grounded",
            "chat_route_reasons": list(route_decision.reasons) if route_decision else [],
            "fallback_used": (
                generation.requested_model_id != generation.used_model_id
            ),
            "finish_reason": "stop",
            "timing": timing,
            "ai_provider": generation.provider,
            "ai_model": generation.used_model_name,
            "embedding_provider": embedding_provider,
            "embedding_model": retrieval_profile.model,
            "embedding_profile_id": retrieval_profile.embedding_profile_id,
            "vector_index_id": retrieval_index.vector_index_id,
            "snapshot_vector_binding_id": retrieval_binding.binding_id,
            "snapshot_id": retrieval_binding.snapshot_id,
            "generation_id": retrieval_binding.generation_id,
            "vector_route_revision": retrieval_runtime["route"].revision,
            "vector_route_mode": retrieval_runtime["route"].routing_mode,
            "vector_target_id": retrieval_target.vector_target_id,
            "index_version": retrieval_index.index_version,
            "canonical_context": {
                "schema_version": canonical_context.schema_version,
                "context_id": canonical_context.context_id,
                "retrieval_owner": canonical_context.retrieval.owner,
                "retrieval_mode": canonical_context.retrieval.mode,
                "prompt_owner": canonical_context.retrieval.prompt_owner,
                "raw_content_retention": canonical_context.retention.policy,
                "source_count": len(canonical_context.sources),
            },
            "retrieval": {
                "policy": retrieval_decision.policy,
                "candidate_k": candidate_k,
                "candidate_count": retrieval_decision.candidate_count,
                "reranked_count": retrieval_decision.reranked_count,
                "selected_count": retrieval_decision.selected_count,
                "context_chars": retrieval_decision.context_chars,
                "requested_reasoning_mode": agentic_trace.requested_mode,
                "reasoning_mode": agentic_trace.mode,
                "step_count": agentic_trace.step_count,
                "max_steps": agentic_trace.max_steps,
                "queries": list(agentic_trace.queries),
                "standalone_query": agentic_trace.standalone_query,
                "follow_up_rewritten": agentic_trace.follow_up_rewritten,
                "context_grounded": agentic_trace.context_grounded,
                "unique_candidate_count": agentic_trace.unique_candidate_count,
                "evidence_coverage": agentic_trace.evidence_coverage,
                "final_novelty_ratio": agentic_trace.final_novelty_ratio,
                "stop_reason": agentic_trace.stop_reason,
                "degraded": retrieval_degraded_error is not None,
                "degraded_error": retrieval_degraded_error,
                "frontend_context_available": usable_frontend_context,
                "client_top_k_ignored": payload.top_k is not None,
            },
            "prompt_budget": {
                "context_window_tokens": settings.ai_context_window_tokens,
                "question_max_chars": settings.ai_question_max_chars,
                "history_max_chars": settings.ai_history_max_chars,
                "frontend_context_max_chars": (
                    settings.ai_frontend_context_max_chars
                ),
                "rag_context_max_chars": settings.rag_context_max_chars,
            },
            "request_normalization": {
                "auto_project_requested": payload.project_id == "__auto__",
                "fallback_session_id": payload.session_id.startswith("vscode-"),
                "accepted_extra_fields": sorted(
                    (payload.model_extra or {}).keys()
                ),
                **intake_result.metadata(debug=payload.debug),
            },
            "requested_snapshot_id": payload.snapshot_id,
            "session_scope": effective_project_id,
            "history_messages": len(payload.history),
            "context_items": (
                1
                if isinstance(payload.context, str) and payload.context.strip()
                else 0
                if isinstance(payload.context, str)
                else len(payload.context)
            ),
            "source_count": len(chat_sources),
            "sources": [
                source.model_dump(mode="json") for source in chat_sources
            ],
        },
    )


async def _chat_sse_body(
    payload: ChatRequest,
    request: Request,
):
    request_id = _request_id(request)
    loop = asyncio.get_running_loop()
    deltas: asyncio.Queue[str] = asyncio.Queue()
    emitted_delta = False

    def on_delta(text: str) -> None:
        if text:
            loop.call_soon_threadsafe(deltas.put_nowait, text)

    non_streaming_payload = payload.model_copy(update={"stream": False})

    def execute() -> ChatResponse:
        return _chat_json(
            non_streaming_payload,
            request,
            delta_callback=on_delta,
        )

    task = asyncio.create_task(run_in_threadpool(execute))
    sequence = 1
    yield _sse_event(
        "meta",
        request_id,
        sequence,
        {
            "context_id": (
                getattr(request.state, "vision_chat_context", None).context_id
                if getattr(request.state, "vision_chat_context", None)
                else None
            ),
            "streaming": True,
        },
    )
    sequence += 1
    yield _sse_event(
        "status",
        request_id,
        sequence,
        {"message": "AI 응답을 준비하고 있습니다."},
    )
    sequence += 1

    try:
        while not task.done() or not deltas.empty():
            if not deltas.empty():
                fragment = deltas.get_nowait()
            else:
                delta_waiter = asyncio.create_task(deltas.get())
                completed, _pending = await asyncio.wait(
                    {task, delta_waiter},
                    timeout=15.0,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if delta_waiter in completed:
                    fragment = delta_waiter.result()
                else:
                    delta_waiter.cancel()
                    try:
                        await delta_waiter
                    except asyncio.CancelledError:
                        pass
                    if task in completed:
                        continue
                    # SSE comments keep proxy and browser connections alive without
                    # changing the public event state machine.
                    yield ": keep-alive\n\n"
                    continue
            emitted_delta = True
            yield _sse_event(
                "delta",
                request_id,
                sequence,
                {"text": fragment},
                simulated=False,
                progress_source="ai-server",
            )
            sequence += 1
        response = await task
    except HTTPException as exc:
        error_message = (
            exc.detail
            if isinstance(exc.detail, str)
            else json.dumps(exc.detail, ensure_ascii=False, default=str)
        )
        yield _sse_event(
            "error",
            request_id,
            sequence,
            {
                "code": _error_code(exc.status_code),
                "message": error_message,
                # Keep the canonical message field and expose the legacy error
                # alias so older VS Code clients can render the real cause.
                "error": error_message,
                "status_code": exc.status_code,
                "retryable": exc.status_code in {429, 502, 503, 504},
            },
        )
        return
    except Exception as exc:  # pragma: no cover - final stream safety boundary
        logger.exception("Unhandled Chat SSE failure request_id=%s", request_id)
        error_message = str(exc) or "Chat Streaming 처리에 실패했습니다."
        yield _sse_event(
            "error",
            request_id,
            sequence,
            {
                "code": "INTERNAL_ERROR",
                "message": error_message,
                "error": error_message,
                "status_code": 500,
                "retryable": False,
            },
        )
        return

    if not emitted_delta and response.answer:
        # Providers without token Streaming still use the same SSE contract.
        yield _sse_event(
            "delta",
            request_id,
            sequence,
            {"text": response.answer},
            simulated=True,
            progress_source="vision-generator",
        )
        sequence += 1

    response = response.model_copy(
        update={
            "metadata": {
                **response.metadata,
                "transport": "sse",
                "streaming_mode": (
                    "upstream_delta" if emitted_delta else "buffered_compatibility"
                ),
            }
        }
    )
    _cache_chat_response(request, response)
    yield _sse_event(
        "done",
        request_id,
        sequence,
        response.model_dump(mode="json"),
        simulated=not emitted_delta,
        progress_source=("ai-server" if emitted_delta else "vision-generator"),
    )


@app.post(
    "/v1/chat",
    response_model=ChatResponse,
    tags=["Chat"],
    summary="Run general or separately-contextualized Chat",
    description=(
        "The minimal body is role, model_id, content and stream. Project, Git Commit "
        "and Snapshot data may be registered independently through POST /v1/chat/contexts "
        "and selected with X-Vision-Context-ID. Without that header Chat remains unscoped. "
        "stream=false returns JSON; stream=true returns meta/status/delta/done/error SSE."
    ),
    responses=ERROR_RESPONSES,
)
def chat(payload: ChatRequest, request: Request) -> Any:
    contextualized = _apply_registered_chat_context(payload, request)
    sse_requested = wants_chat_sse(
        stream=contextualized.stream,
        input_fields=contextualized.intake_input_fields,
        accept_header=request.headers.get("accept"),
    )
    logger.info(
        "Chat transport request_id=%s selected=%s stream=%r explicit_stream=%s accept=%r",
        _request_id(request),
        "sse" if sse_requested else "json",
        contextualized.stream,
        "stream" in contextualized.intake_input_fields,
        request.headers.get("accept"),
    )
    if sse_requested:
        return StreamingResponse(
            _chat_sse_body(contextualized, request),
            media_type="text/event-stream",
            headers={
                # Some existing Frontend builds compare this value literally
                # instead of parsing the media type and optional parameters.
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "X-Content-Type-Options": "nosniff",
                "X-Vision-Chat-Transport": "sse",
                "Vary": "Accept",
            },
        )
    response = _chat_json(contextualized, request)
    _cache_chat_response(request, response)
    return response


@app.post(
    "/v1/uploads",
    response_model=UploadSessionResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Uploads"],
)
def create_upload(payload: UploadCreateRequest) -> UploadProgressResponse:
    return upload_manager.create(payload)


@app.post(
    "/v1/uploads/{upload_id}/manifest",
    response_model=UploadProgressResponse,
    tags=["Uploads"],
)
def add_upload_manifest(
    upload_id: str, payload: UploadManifestPageRequest
) -> UploadProgressResponse:
    try:
        return upload_manager.add_manifest(upload_id, payload)
    except UploadError as exc:
        raise _upload_error(exc) from exc


@app.put("/v1/uploads/{upload_id}/files/{file_id}/parts/{part_number}")
async def upload_file_part(
    upload_id: str,
    file_id: str,
    part_number: int,
    request: Request,
) -> dict[str, Any]:
    try:
        return await upload_manager.write_part(
            upload_id,
            file_id,
            part_number,
            request.headers.get("content-range"),
            request.headers.get("digest") or request.headers.get("x-content-sha256"),
            request.stream(),
        )
    except UploadError as exc:
        raise _upload_error(exc) from exc


@app.get(
    "/v1/uploads/{upload_id}",
    response_model=UploadProgressResponse,
)
def get_upload(upload_id: str) -> UploadProgressResponse:
    try:
        return upload_manager.get(upload_id)
    except UploadError as exc:
        raise _upload_error(exc) from exc


@app.post(
    "/v1/uploads/{upload_id}/complete",
    response_model=IndexingJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def complete_upload(
    upload_id: str,
) -> IndexingJobResponse:
    try:
        job_id, upload = upload_manager.queue(upload_id)
        version_info = upload_manager.version_info(upload_id)
        upload_git = (
            GitVersionInfo.model_validate(version_info["git"])
            if version_info.get("git")
            else None
        )
        upload_modified_at = (
            datetime.fromisoformat(version_info["modified_at"])
            if version_info.get("modified_at")
            else None
        )
        upload_repository_id = f"upload:{upload.project_id}"
        snapshot_repository.upsert_repository(
            repository_id=upload_repository_id,
            tenant_id=settings.snapshot_tenant_id,
            project_id=upload.project_id,
            source_type="frontend-upload",
            repository_url=None,
            default_branch=(upload_git.branch if upload_git else None),
        )
        upload_fingerprint = snapshot_fingerprint(
            tenant_id=settings.snapshot_tenant_id,
            repository_id=upload_repository_id,
            snapshot_kind="upload",
            revision=(upload_git.commit_sha if upload_git else None),
            manifest_sha256=version_info.get("manifest_sha256"),
        )
        snapshot_repository.register_snapshot(
            snapshot_id=upload.snapshot_id,
            tenant_id=settings.snapshot_tenant_id,
            repository_id=upload_repository_id,
            project_id=upload.project_id,
            snapshot_kind="upload",
            revision=(upload_git.commit_sha if upload_git else None),
            branch=(upload_git.branch if upload_git else None),
            dirty=(upload_git.dirty if upload_git else None),
            committed_at=(upload_git.committed_at if upload_git else None),
            tree_sha=None,
            manifest_sha256=version_info.get("manifest_sha256"),
            fingerprint=upload_fingerprint,
            verified_by="frontend",
            locator={"provider": "frontend-upload", "upload_id": upload_id},
            status="captured",
            file_count=int(version_info.get("document_count") or 0),
            total_bytes=int(version_info.get("total_bytes") or 0),
        )
        project_store.register_snapshot(
            upload.project_id,
            upload.snapshot_id,
            manifest_sha256=version_info.get("manifest_sha256"),
            modified_at=upload_modified_at,
            git=upload_git,
        )
    except UploadError as exc:
        raise _upload_error(exc) from exc
    except ProjectStoreError as exc:
        raise _project_store_error() from exc
    except SnapshotRepositoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    if upload.status != "completed":
        try:
            _enqueue_worker_task(
                "upload.process",
                {"upload_id": upload_id},
                dedupe_key=f"upload.process:{job_id}",
            )
        except HTTPException:
            try:
                upload_manager.mark_queue_failed(
                    upload_id,
                    "Redis indexing queue was unavailable before dispatch",
                )
            except UploadError:
                logger.exception(
                    "Failed to mark upload queue dispatch as failed upload_id=%s",
                    upload_id,
                )
            raise
    return IndexingJobResponse(
        job_id=job_id,
        upload_id=upload_id,
        project_id=upload.project_id,
        status=upload.status,
        status_url=f"/v1/indexing-jobs/{job_id}",
    )


@app.get(
    "/v1/indexing-jobs",
    response_model=IndexingJobListResponse,
    tags=["Documents"],
    summary="List repository and Frontend upload indexing progress",
)
def list_indexing_jobs(
    project_id: str | None = Query(default=None, min_length=1, max_length=255),
    active_only: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=200),
) -> IndexingJobListResponse:
    try:
        repository_jobs = repository_store.list_jobs(
            project_id=project_id,
            active_only=active_only,
            limit=limit,
        )
    except RepositoryStoreError as exc:
        raise _repository_store_error(exc) from exc
    upload_jobs = upload_manager.list_index_jobs(
        project_id=project_id,
        active_only=active_only,
        limit=limit,
    )
    jobs = [
        *(_repository_job_summary(row) for row in repository_jobs),
        *(_upload_job_summary(state) for state in upload_jobs),
    ]
    jobs.sort(key=lambda job: job.updated_at, reverse=True)
    jobs = jobs[:limit]
    return IndexingJobListResponse(
        checked_at=datetime.now(timezone.utc),
        jobs=jobs,
        total=len(jobs),
        active=sum(job.active for job in jobs),
    )


@app.get(
    "/v1/indexing-jobs/{job_id}",
    response_model=UploadProgressResponse | RepositoryIndexJobResponse,
    tags=["Documents"],
    summary="Read one indexing job",
)
def get_indexing_job(
    job_id: str,
) -> UploadProgressResponse | RepositoryIndexJobResponse:
    if not job_id.startswith("job_"):
        raise HTTPException(status_code=404, detail="인덱싱 작업을 찾을 수 없습니다.")
    try:
        repository_job = repository_store.get_job(job_id)
    except RepositoryStoreError as exc:
        raise _repository_store_error(exc) from exc
    if repository_job is not None:
        return _repository_job_response(repository_job)
    for session_file in settings.upload_root.glob("upl_*/session.json"):
        try:
            state = json.loads(session_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if state.get("job_id") == job_id:
            return upload_manager.get(state["upload_id"])
    raise HTTPException(status_code=404, detail="인덱싱 작업을 찾을 수 없습니다.")


@app.delete("/v1/uploads/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_upload(upload_id: str) -> None:
    try:
        upload_manager.cancel(upload_id)
    except UploadError as exc:
        raise _upload_error(exc) from exc


@app.delete("/v1/projects/{project_id}/documents")
def delete_project_documents(project_id: str) -> dict[str, Any]:
    try:
        deleted_chunks = vector_store.delete_project(project_id)
        deleted_registry = project_store.delete_project(project_id)
    except VectorStoreError as exc:
        raise _service_error(exc) from exc
    except ProjectStoreError as exc:
        raise _project_store_error() from exc
    return {
        "project_id": project_id,
        "deleted_chunks": deleted_chunks,
        "deleted_registry": deleted_registry,
    }


@app.post("/extension/chat", response_model=ChatResponse, include_in_schema=False)
def extension_chat(payload: ChatRequest, request: Request) -> ChatResponse:
    return chat(payload, request)


@app.post("/ingest", include_in_schema=False)
def legacy_ingest(payload: LegacyIngestRequest) -> dict[str, Any]:
    response = ingest_documents(
        IngestRequest(
            project_id="default",
            documents=[
                DocumentInput(
                    document_id=payload.document_id,
                    text=payload.text,
                    metadata=payload.metadata,
                )
            ],
        )
    )
    return {"message": "document ingested", **response.model_dump()}


@app.post("/search", include_in_schema=False)
def legacy_search(payload: LegacyQueryRequest) -> dict[str, Any]:
    response = search_documents(
        SearchRequest(project_id="default", query=payload.query, top_k=payload.top_k)
    )
    return {"query": payload.query, "results": [item.model_dump() for item in response.results]}


@app.post("/chat", include_in_schema=False)
def legacy_chat(payload: LegacyQueryRequest) -> dict[str, Any]:
    search_response = search_documents(
        SearchRequest(project_id="default", query=payload.query, top_k=payload.top_k)
    )
    try:
        answer, _provider = chat_service.answer(
            payload.query, search_response.results, []
        )
    except ServiceError as exc:
        raise _service_error(exc) from exc
    return {
        "query": payload.query,
        "answer": answer,
        "results": [item.model_dump() for item in search_response.results],
    }
