from __future__ import annotations

import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from ipaddress import ip_address, ip_network
from time import perf_counter
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .ai_providers import (
    AIProvider,
    AIProviderRegistry,
    AIProviderStoreError,
    PostgresAIProviderStore,
)
from .config import settings
from .connectivity import ConnectivityStoreError, PostgresConnectivityStore
from .distributed import DistributedStateError, RedisCoordinator
from .frontend_clients import (
    FrontendClient,
    FrontendClientStoreError,
    PostgresFrontendClientStore,
)
from .generation import GenerationRouter
from .metadata_store import MetadataStoreError, PostgresMetadataStore
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
from .project_store import PostgresProjectStore, ProjectStoreError
from .repository_indexer import RepositoryIndexer
from .repository_store import PostgresRepositoryStore, RepositoryStoreError
from .schemas import (
    ChatRequest,
    ChatResponse,
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
    CommunicationEvent,
    CommunicationEventListResponse,
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
)
from .services import ChatService, EmbeddingService, ServiceError
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
from .vector_store import QdrantVectorStore, SQLiteVectorStore, VectorStoreError


app = FastAPI(
    title="VS Code AI Assistant Backend",
    version="3.0.0",
    description=(
        "Frozen public API v1 for VS Code repository ingest, BGE-M3/Qdrant RAG, "
        "and BackendAI, NVIDIA, or Groq answer generation. Canonical schema version: 1.0."
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
    expose_headers=["X-Request-ID", "X-API-Version"],
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
    ("GET", "/v1/IngestResponse"),
    ("GET", "/v1/repositories"),
    ("GET", "/v1/repositories/{source_id}/tree"),
    ("GET", "/v1/projects/{project_id}/tree"),
    ("GET", "/v1/indexing-jobs"),
    ("POST", "/v1/client-heartbeat"),
    ("POST", "/v1/documents/ingest"),
    ("POST", "/v1/projects/{project_id}/version/check"),
    ("POST", "/v1/chat"),
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
        and path.rstrip("/") == "/v1/chat"
        and client_type not in {"admin-dashboard", "admin-playground"}
    )
    managed_frontend_request = (
        bool(client_id)
        or bool(instance_id)
        or client_type == "vscode-extension"
        or auto_enrollment_request
    )
    monitored_frontend_endpoint = (
        request.method,
        normalized_activity_path,
    ) in MONITORED_FRONTEND_ENDPOINTS
    dashboard_request = client_type in {"admin-dashboard", "admin-playground"}
    source_host = _frontend_source_ip(request)
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
        or path in {"/", "/v1/health", "/openapi.json", "/docs", "/redoc"}
        or path.startswith("/v1/admin/")
    )
    if managed_frontend_request and not guard_exempt:
        try:
            decision = await run_in_threadpool(
                frontend_client_store.authorize_or_register,
                client_id=client_id or None,
                instance_id=instance_id or None,
                source_ip=source_host,
                auto_register=auto_enrollment_request,
                client_name=(
                    request.headers.get("x-client-name")
                    or request.headers.get("user-agent")
                ),
            )
        except FrontendClientStoreError:
            return await finalize_response(
                _error_response(
                    request,
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Frontend client access registry is unavailable",
                )
            )
        if not decision.allowed:
            return await finalize_response(
                _error_response(
                    request,
                    status.HTTP_403_FORBIDDEN,
                    decision.reason,
                )
            )
        if decision.client is not None:
            request.state.frontend_client_id = decision.client.client_id
            request.state.frontend_client_auto_registered = (
                decision.auto_registered
            )
            telemetry_client_id = decision.client.client_id
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

runtime_service_store = PostgresRuntimeServiceSettingsStore(settings)
settings = runtime_service_store.effective_settings(settings)
embedding_service = EmbeddingService(settings)
chat_service = ChatService(settings)
runtime_network_store = PostgresRuntimeNetworkSettingsStore(settings)
model_access_store = PostgresModelAccessPolicyStore(settings)
ai_provider_store = PostgresAIProviderStore(settings)
ai_provider_registry = AIProviderRegistry(
    ai_provider_store,
    settings,
    model_access_store.is_enabled,
)
generation_router = GenerationRouter(
    settings,
    backendai_base_url_provider=runtime_network_store.backendai_base_url,
    groq_settings_provider=runtime_service_store.groq_settings,
    model_enabled_provider=model_access_store.is_enabled,
    custom_provider_registry=ai_provider_registry,
)
if settings.vector_db_provider == "sqlite":
    vector_store = SQLiteVectorStore(settings.vector_db_path)
elif settings.vector_db_provider == "qdrant":
    vector_store = QdrantVectorStore(
        settings.qdrant_url,
        settings.qdrant_api_key,
        settings.qdrant_collection,
        settings.embedding_dimension,
        settings.index_version,
        settings.request_timeout_seconds,
    )
else:
    raise RuntimeError(f"지원하지 않는 VECTOR_DB_PROVIDER: {settings.vector_db_provider}")
metadata_store = PostgresMetadataStore(settings)
project_store = PostgresProjectStore(settings)
repository_store = PostgresRepositoryStore(settings)
offline_embedding_importer = OfflineEmbeddingImporter(
    settings,
    repository_store,
    vector_store,
)
local_project_registry = LocalProjectRegistry(settings.project_db_local_root)
upload_manager = UploadManager(settings)
connectivity_store = PostgresConnectivityStore(settings)
frontend_client_store = PostgresFrontendClientStore(settings)
redis_coordinator = RedisCoordinator(settings)
repository_indexer = RepositoryIndexer(
    settings,
    repository_store,
    embedding_service,
    vector_store,
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
    value: RuntimeNetworkSettings,
) -> NetworkSettingsResponse:
    frontend_probe = runtime_network_store.probe_frontend()
    return NetworkSettingsResponse(
        frontend=NetworkEndpointSettings(
            ip=value.frontend.ip,
            port=value.frontend.port,
        ),
        backendai=NetworkEndpointSettings(
            ip=value.backendai.ip,
            port=value.backendai.port,
        ),
        updated_at=value.updated_at,
        frontend_reachable=frontend_probe["reachable"],
        frontend_latency_ms=frontend_probe["latency_ms"],
        frontend_error=frontend_probe["error"],
    )


def _runtime_service_settings_response(
    value: RuntimeServiceSettings,
) -> RuntimeServiceSettingsResponse:
    active_vector_url = urlsplit(settings.qdrant_url)
    active_host = active_vector_url.hostname or "qdrant"
    active_port = active_vector_url.port or (
        443 if active_vector_url.scheme == "https" else 6333
    )
    vector_restart_required = any(
        (
            value.vector.host != active_host,
            value.vector.port != active_port,
            value.vector.collection != settings.qdrant_collection,
            value.vector.embedding_deployment
            != settings.embedding_deployment,
            value.vector.embedding_provider != settings.embedding_provider,
            value.vector.embedding_base_url != settings.embedding_base_url,
            value.vector.embedding_model != settings.embedding_model,
            value.vector.embedding_model_id != settings.embedding_model_id,
            value.vector.embedding_dimension != settings.embedding_dimension,
            value.vector.embedding_batch_size
            != settings.embedding_batch_size,
            value.vector.index_version != settings.index_version,
        )
    )
    vector_reindex_required = any(
        (
            value.vector.collection != settings.qdrant_collection,
            value.vector.embedding_model_id != settings.embedding_model_id,
            value.vector.embedding_model != settings.embedding_model,
            value.vector.embedding_dimension != settings.embedding_dimension,
            value.vector.index_version != settings.index_version,
        )
    )
    return RuntimeServiceSettingsResponse(
        groq=RuntimeGroqSettingsResponse(
            enabled=value.groq.enabled,
            base_url=value.groq.base_url,
            model=value.groq.model,
            public_model_id=settings.groq_public_model_id,
            api_key_configured=bool(settings.groq_api_key),
        ),
        default_model_id=value.groq.default_model_id,
        vector=RuntimeVectorSettingsResponse(
            provider=settings.vector_db_provider,
            host=value.vector.host,
            port=value.vector.port,
            collection=value.vector.collection,
            embedding_deployment=value.vector.embedding_deployment,
            embedding_provider=value.vector.embedding_provider,
            embedding_base_url=value.vector.embedding_base_url,
            embedding_model=value.vector.embedding_model,
            embedding_model_id=value.vector.embedding_model_id,
            embedding_dimension=value.vector.embedding_dimension,
            embedding_batch_size=value.vector.embedding_batch_size,
            index_version=value.vector.index_version,
            active_host=active_host,
            active_port=active_port,
            active_collection=settings.qdrant_collection,
            active_embedding_deployment=settings.embedding_deployment,
            active_embedding_provider=settings.embedding_provider,
            active_embedding_base_url=settings.embedding_base_url,
            active_embedding_model=settings.embedding_model,
            active_embedding_model_id=settings.embedding_model_id,
            active_embedding_dimension=settings.embedding_dimension,
            active_embedding_batch_size=settings.embedding_batch_size,
            active_index_version=settings.index_version,
            restart_required=vector_restart_required,
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
    supplied = (request.headers.get("x-client-id") or "").strip()
    if supplied:
        return supplied[:255]
    client_type = (request.headers.get("x-client-type") or "").strip().lower()
    if client_type == "vscode-extension":
        return f"vscode:{project_id}"[:255]
    return None


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


@app.get("/v1/health", tags=["System"])
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "vs-code-ai-assistant-backend",
        "version": "3.0.0",
        "configuration": settings.public_status(),
        "vector_store": vector_store.stats(),
        "metadata_store": metadata_store.status(),
        "project_store": project_store.status(),
        "message": "백엔드 API 서버에서 응답중 입니다."
    }
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
def list_models() -> ModelListResponse:
    models = [
        model for model in generation_router.models()
        if model.enabled
    ]
    return ModelListResponse(
        default_model_id=generation_router.default_model_id,
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
    generation_router.invalidate_backendai_status()
    return _network_settings_response(value)


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


@app.get(
    "/v1/admin/models",
    response_model=ModelListResponse,
    tags=["System"],
    summary="List every discovered generation model and its API access policy",
)
def list_admin_models(request: Request) -> ModelListResponse:
    _require_admin_proxy(request)
    return ModelListResponse(
        default_model_id=generation_router.default_model_id,
        checked_at=datetime.now(timezone.utc),
        models=generation_router.models(),
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
    summary="Scan known machines for Ollama and register chat-capable servers",
)
def scan_known_ollama_servers(request: Request) -> OllamaScanResponse:
    """Scan explicit, known hosts only; this never sweeps the whole LAN."""

    _require_admin_proxy(request)
    candidates: dict[str, str] = {
        "http://host.docker.internal:11434": "API Server Windows host",
        "http://127.0.0.1:11434": "FastAPI process host",
    }
    legacy_backendai_url = runtime_network_store.backendai_base_url().rstrip("/")
    candidates[legacy_backendai_url] = "Configured AI Model Server"
    try:
        for client in frontend_client_store.list(refresh=True):
            if client.enabled:
                candidates.setdefault(
                    f"http://{client.ip}:11434",
                    f"Frontend Client · {client.name}",
                )
    except FrontendClientStoreError:
        # Local and configured AI Server discovery remains useful even if the
        # optional frontend registry is temporarily unavailable.
        pass

    try:
        existing_providers = {
            provider.base_url.rstrip("/"): provider
            for provider in ai_provider_store.list()
        }
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
        value = runtime_service_store.update(
            groq_enabled=payload.groq.enabled,
            groq_base_url=payload.groq.base_url,
            groq_model=payload.groq.model,
            default_model_id=payload.default_model_id,
            vector_host=payload.vector.host,
            vector_port=payload.vector.port,
            vector_collection=payload.vector.collection,
            embedding_deployment=payload.vector.embedding_deployment,
            embedding_provider=payload.vector.embedding_provider,
            embedding_base_url=payload.vector.embedding_base_url,
            embedding_model=payload.vector.embedding_model,
            embedding_model_id=payload.vector.embedding_model_id,
            embedding_dimension=payload.vector.embedding_dimension,
            embedding_batch_size=payload.vector.embedding_batch_size,
            index_version=payload.vector.index_version,
        )
    except RuntimeServiceSettingsError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL runtime service settings are unavailable",
        ) from exc
    generation_router.invalidate_groq_status()
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
    summary="Queue a repository snapshot and BGE-M3 index generation",
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
        if "no active index generation" in str(exc):
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
        if "no active index generation" in str(exc):
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
    try:
        active = repository_store.get_active_generation(project_id)
        if active is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project has no active index generation",
            )
        postgres_chunks = repository_store.generation_chunk_count(
            active["generation_id"]
        )
        qdrant_chunks = vector_store.count_generation(
            project_id, active["generation_id"]
        )
    except RepositoryStoreError as exc:
        raise _repository_store_error(exc) from exc
    except VectorStoreError as exc:
        raise _service_error(exc) from exc
    return VectorIndexValidationResponse(
        project_id=project_id,
        snapshot_id=active["snapshot_id"],
        generation_id=active["generation_id"],
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


@app.post("/v1/search", response_model=SearchResponse, tags=["Documents"])
def search_documents(payload: SearchRequest) -> SearchResponse:
    try:
        embedding = embedding_service.embed(payload.query, input_type="query")
        active_generation = (
            repository_store.get_active_generation(payload.project_id)
            if repository_store.configured
            else None
        )
        results = vector_store.search(
            payload.project_id,
            embedding.vector,
            embedding.provider,
            embedding.model,
            payload.top_k,
            (
                active_generation["generation_id"]
                if active_generation is not None
                else None
            ),
        )
        results = project_store.enrich_sources(results)
    except (ServiceError, VectorStoreError) as exc:
        raise _service_error(exc) from exc
    except (ProjectStoreError, RepositoryStoreError) as exc:
        raise _project_store_error() from exc
    return SearchResponse(
        project_id=payload.project_id,
        query=payload.query,
        results=results,
        embedding_provider=embedding.provider,
    )


@app.post(
    "/v1/chat",
    response_model=ChatResponse,
    tags=["Chat"],
    summary="Run project-scoped RAG chat",
    description=(
        "Searches the project with BGE-M3/Qdrant, assembles numbered sources, "
        "calls the selected public model_id, and returns answer plus sources[]. "
        "The canonical question field is message; prompt is a v1 compatibility alias."
    ),
    responses=ERROR_RESPONSES,
)
def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    # The frontend sends the workspace folder name as both project_id and
    # session_id. Neither identifier is hard-coded by the server.
    overall_started = perf_counter()
    effective_project_id = payload.project_id
    effective_top_k = payload.top_k if payload.top_k is not None else 5
    request_id = _request_id(request)
    telemetry_client_id = _frontend_client_id(request, effective_project_id)
    _record_frontend_activity(request, effective_project_id, "chat.request")
    if payload.stream:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="stream=true is not supported yet; use stream=false",
        )
    retrieval_started = perf_counter()
    _safe_record_communication_event(
        request_id=request_id,
        channel="rag",
        direction="fastapi_to_vectordb",
        phase="rag.request",
        status="started",
        client_id=telemetry_client_id,
        project_id=effective_project_id,
        provider=settings.vector_db_provider,
        model=settings.embedding_model,
        details={"top_k": effective_top_k},
    )
    try:
        search_response = search_documents(
            SearchRequest(
                project_id=effective_project_id,
                query=payload.message,
                top_k=effective_top_k,
            )
        )
    except HTTPException as exc:
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
            provider=settings.vector_db_provider,
            model=settings.embedding_model,
            error=str(exc),
            details={"top_k": effective_top_k},
        )
        raise
    retrieval_ms = round((perf_counter() - retrieval_started) * 1000)
    _safe_record_communication_event(
        request_id=request_id,
        channel="rag",
        direction="vectordb_to_fastapi",
        phase="rag.response",
        status="success",
        client_id=telemetry_client_id,
        project_id=effective_project_id,
        status_code=200,
        duration_ms=retrieval_ms,
        provider=search_response.embedding_provider,
        model=settings.embedding_model,
        source_count=len(search_response.results),
        details={"top_k": effective_top_k},
    )
    generation_started = perf_counter()
    requested_model_id = payload.model_id or generation_router.default_model_id
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
        source_count=len(search_response.results),
        details={"rag_sources_attached": len(search_response.results)},
    )
    try:
        generation = generation_router.generate(
            payload.model_id,
            payload.message,
            search_response.results,
            payload.history,
            payload.context,
            effective_project_id,
            payload.session_id,
            request_id=request_id,
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
            source_count=len(search_response.results),
            error=str(exc),
            details={"rag_sources_attached": len(search_response.results)},
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
        source_count=len(search_response.results),
        details={
            "answer_chars": len(generation.answer),
            "rag_sources_attached": len(search_response.results),
        },
    )
    total_ms = round((perf_counter() - overall_started) * 1000)
    chat_sources = [
        source.model_copy(update={"citation_id": index})
        for index, source in enumerate(search_response.results, start=1)
    ]
    timing = {
        "retrieval_ms": retrieval_ms,
        "generation_ms": generation_ms,
        "total_ms": total_ms,
    }
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
            "fallback_used": (
                generation.requested_model_id != generation.used_model_id
            ),
            "finish_reason": "stop",
            "timing": timing,
            "ai_provider": generation.provider,
            "ai_model": generation.used_model_name,
            "embedding_provider": search_response.embedding_provider,
            "embedding_model": settings.embedding_model,
            "index_version": settings.index_version,
            "top_k": effective_top_k,
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
