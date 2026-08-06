from __future__ import annotations


import os
from collections.abc import Callable
from typing import Literal, Protocol
from urllib.parse import urlsplit


from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator


from .config import Settings
from .snapshots.contracts import (
    AccessPlan,
    LocatorRecord,
    RepositoryRecord,
    SnapshotFileResponse,
    SnapshotRegistrationResponse,
    SnapshotRecord,
    SnapshotTreeResponse,
    normalize_repository_full_name,
)
from .snapshots.service import GithubSnapshotService, GithubSnapshotServiceError




class SnapshotAdminCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")


    repositories: int = Field(..., ge=0)
    snapshots: int = Field(..., ge=0)
    locators: int = Field(..., ge=0)




class SnapshotAdminStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


    overall_status: Literal["ready", "degraded"]
    feature_enabled: bool
    public_routes_exposed: bool
    token_configured: bool
    tenant_id: str
    allowed_repositories: list[str]
    database_ready: bool
    table_count: int = Field(..., ge=0, le=3)
    counts: SnapshotAdminCounts
    error: str | None = None




class SnapshotAdminOverviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


    status: SnapshotAdminStatusResponse
    repositories: list[RepositoryRecord]
    snapshots: list[SnapshotRecord]
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)
    total_repositories: int = Field(..., ge=0)
    total_snapshots: int = Field(..., ge=0)
    total_locators: int = Field(..., ge=0)
    has_more_repositories: bool
    has_more_snapshots: bool




class SnapshotAdminDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


    repository: RepositoryRecord
    snapshot: SnapshotRecord
    locator: LocatorRecord | None
    access_plan: AccessPlan




class SnapshotAdminImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


    repository_url: str = Field(..., min_length=3, max_length=500)
    ref: str | None = Field(default=None, max_length=255)


    @field_validator("repository_url")
    @classmethod
    def normalize_repository_url(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or "\x00" in normalized:
            raise ValueError("repository_url must not be blank")
        return normalized


    @field_validator("ref")
    @classmethod
    def normalize_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if "\x00" in normalized:
            raise ValueError("ref contains an invalid character")
        return normalized or None




class SnapshotAdminImportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


    repository: RepositoryRecord
    snapshot: SnapshotRecord
    deduplicated: bool
    resolved_ref: str




class SnapshotAdminService(Protocol):
    @property
    def tenant_id(self) -> str: ...


    def import_public_repository_snapshot(
        self,
        repository_full_name: str,
        ref: str | None = None,
    ) -> tuple[RepositoryRecord, SnapshotRegistrationResponse, str]: ...


    def admin_status(self) -> dict[str, int]: ...


    def list_repositories(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RepositoryRecord]: ...


    def list_snapshots_for_tenant(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SnapshotRecord]: ...


    def get_repository(self, repository_id: str) -> RepositoryRecord: ...


    def get_snapshot(self, snapshot_id: str) -> SnapshotRecord: ...


    def get_locator(self, snapshot_id: str) -> LocatorRecord | None: ...


    def resolve(self, snapshot_id: str) -> AccessPlan: ...


    def tree(self, snapshot_id: str) -> SnapshotTreeResponse: ...


    def file(self, snapshot_id: str, path: str) -> SnapshotFileResponse: ...




AdminProxyGuard = Callable[[Request], None]
ServiceFactory = Callable[[], SnapshotAdminService]




def _repository_full_name_from_url(value: str) -> str:
    raw = value.strip()
    if not raw or "\x00" in raw:
        raise ValueError("GitHub repository address must not be blank")


    if "://" not in raw:
        lowered = raw.casefold()
        if lowered.startswith("github.com/") or lowered.startswith("www.github.com/"):
            raw = "https://" + raw
        else:
            candidate = raw.strip("/")
            if candidate.casefold().endswith(".git"):
                candidate = candidate[:-4]
            return normalize_repository_full_name(candidate)


    parts = urlsplit(raw)
    host = (parts.hostname or "").casefold()
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("GitHub repository address contains an invalid port") from exc


    if parts.scheme.casefold() != "https":
        raise ValueError("Only HTTPS GitHub repository addresses are accepted")
    if host not in {"github.com", "www.github.com"}:
        raise ValueError("Only github.com public repository addresses are accepted")
    if parts.username is not None or parts.password is not None or port is not None:
        raise ValueError("GitHub repository address must not contain credentials or a port")
    if parts.query or parts.fragment:
        raise ValueError("GitHub repository address must not contain a query or fragment")


    repository_path = parts.path.strip("/")
    if repository_path.casefold().endswith(".git"):
        repository_path = repository_path[:-4]
    if len(repository_path.split("/")) != 2:
        raise ValueError("GitHub repository address must use https://github.com/owner/name")
    return normalize_repository_full_name(repository_path)




def _feature_enabled() -> bool:
    return os.getenv("SNAPSHOT_CONTROL_PLANE_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }




def _token_configured() -> bool:
    token = os.getenv("SNAPSHOT_MVP_TOKEN", "")
    return len(token.encode("utf-8")) >= 32




def _allowed_repositories() -> list[str]:
    values = {
        item.strip()
        for item in os.getenv(
            "SNAPSHOT_ALLOWED_REPOSITORIES",
            "h5vision/api_test",
        ).split(",")
        if item.strip()
    }
    return sorted(values, key=str.casefold)




def _status_from_service(service: SnapshotAdminService) -> SnapshotAdminStatusResponse:
    counts = service.admin_status()
    table_count = int(counts.get("table_count", 0))
    database_ready = table_count == 3
    error = None if database_ready else "Snapshot migration tables are incomplete"
    return SnapshotAdminStatusResponse(
        overall_status="ready" if database_ready else "degraded",
        feature_enabled=_feature_enabled(),
        public_routes_exposed=_feature_enabled(),
        token_configured=_token_configured(),
        tenant_id=service.tenant_id,
        allowed_repositories=_allowed_repositories(),
        database_ready=database_ready,
        table_count=table_count,
        counts=SnapshotAdminCounts(
            repositories=int(counts.get("repositories", 0)),
            snapshots=int(counts.get("snapshots", 0)),
            locators=int(counts.get("locators", 0)),
        ),
        error=error,
    )




def _raise_service_error(exc: GithubSnapshotServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc




def create_admin_snapshot_router(
    settings: Settings,
    require_admin_proxy: AdminProxyGuard,
    *,
    service_factory: ServiceFactory | None = None,
) -> APIRouter:
    router = APIRouter(
        prefix="/v1/admin/snapshots",
        tags=["System"],
        include_in_schema=False,
    )


    def service() -> SnapshotAdminService:
        return service_factory() if service_factory is not None else GithubSnapshotService(settings)


    @router.get("/status", response_model=SnapshotAdminStatusResponse)
    def snapshot_admin_status(request: Request) -> SnapshotAdminStatusResponse:
        require_admin_proxy(request)
        try:
            return _status_from_service(service())
        except GithubSnapshotServiceError as exc:
            return SnapshotAdminStatusResponse(
                overall_status="degraded",
                feature_enabled=_feature_enabled(),
                public_routes_exposed=_feature_enabled(),
                token_configured=_token_configured(),
                tenant_id=os.getenv("SNAPSHOT_TENANT_ID", "vision-default").strip()
                or "vision-default",
                allowed_repositories=_allowed_repositories(),
                database_ready=False,
                table_count=0,
                counts=SnapshotAdminCounts(repositories=0, snapshots=0, locators=0),
                error=str(exc),
            )


    @router.post(
        "/import",
        response_model=SnapshotAdminImportResponse,
        status_code=201,
    )
    def snapshot_admin_import(
        payload: SnapshotAdminImportRequest,
        request: Request,
    ) -> SnapshotAdminImportResponse:
        require_admin_proxy(request)
        try:
            repository_full_name = _repository_full_name_from_url(payload.repository_url)
            repository, registration, resolved_ref = (
                service().import_public_repository_snapshot(
                    repository_full_name,
                    payload.ref,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except GithubSnapshotServiceError as exc:
            _raise_service_error(exc)
        return SnapshotAdminImportResponse(
            repository=repository,
            snapshot=registration.snapshot,
            deduplicated=registration.deduplicated,
            resolved_ref=resolved_ref,
        )




    @router.get("", response_model=SnapshotAdminOverviewResponse)
    def snapshot_admin_overview(
        request: Request,
        page: int = Query(default=1, ge=1, le=100_000),
        page_size: int = Query(default=50, ge=1, le=100),
    ) -> SnapshotAdminOverviewResponse:
        require_admin_proxy(request)
        snapshot_service = service()
        offset = (page - 1) * page_size
        try:
            status_payload = _status_from_service(snapshot_service)
            if not status_payload.database_ready:
                raise GithubSnapshotServiceError(
                    status_payload.error or "Snapshot storage is unavailable",
                    status_code=503,
                )
            repositories = snapshot_service.list_repositories(
                limit=page_size,
                offset=offset,
            )
            snapshots = snapshot_service.list_snapshots_for_tenant(
                limit=page_size,
                offset=offset,
            )
        except GithubSnapshotServiceError as exc:
            _raise_service_error(exc)
        return SnapshotAdminOverviewResponse(
            status=status_payload,
            repositories=repositories,
            snapshots=snapshots,
            page=page,
            page_size=page_size,
            total_repositories=status_payload.counts.repositories,
            total_snapshots=status_payload.counts.snapshots,
            total_locators=status_payload.counts.locators,
            has_more_repositories=offset + len(repositories)
            < status_payload.counts.repositories,
            has_more_snapshots=offset + len(snapshots) < status_payload.counts.snapshots,
        )


    @router.get("/{snapshot_id}", response_model=SnapshotAdminDetailResponse)
    def snapshot_admin_detail(
        snapshot_id: str,
        request: Request,
    ) -> SnapshotAdminDetailResponse:
        require_admin_proxy(request)
        snapshot_service = service()
        try:
            snapshot = snapshot_service.get_snapshot(snapshot_id)
            repository = snapshot_service.get_repository(snapshot.repository_id)
            locator = snapshot_service.get_locator(snapshot_id)
            access_plan = snapshot_service.resolve(snapshot_id)
        except GithubSnapshotServiceError as exc:
            _raise_service_error(exc)
        return SnapshotAdminDetailResponse(
            repository=repository,
            snapshot=snapshot,
            locator=locator,
            access_plan=access_plan,
        )


    @router.get("/{snapshot_id}/resolve", response_model=AccessPlan)
    def snapshot_admin_resolve(snapshot_id: str, request: Request) -> AccessPlan:
        require_admin_proxy(request)
        try:
            return service().resolve(snapshot_id)
        except GithubSnapshotServiceError as exc:
            _raise_service_error(exc)


    @router.get("/{snapshot_id}/tree", response_model=SnapshotTreeResponse)
    def snapshot_admin_tree(snapshot_id: str, request: Request) -> SnapshotTreeResponse:
        require_admin_proxy(request)
        try:
            return service().tree(snapshot_id)
        except GithubSnapshotServiceError as exc:
            _raise_service_error(exc)


    @router.get("/{snapshot_id}/file", response_model=SnapshotFileResponse)
    def snapshot_admin_file(
        snapshot_id: str,
        request: Request,
        path: str = Query(..., min_length=1, max_length=2048),
    ) -> SnapshotFileResponse:
        require_admin_proxy(request)
        try:
            return service().file(snapshot_id, path)
        except (GithubSnapshotServiceError, ValueError) as exc:
            if isinstance(exc, GithubSnapshotServiceError):
                _raise_service_error(exc)
            raise HTTPException(status_code=422, detail=str(exc)) from exc


    return router
