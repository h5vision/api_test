param(
    [string]$ProjectRoot = "C:\Users\PC2412\Documents\HancomAI5\Vision",
    [switch]$Force
)


$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest


function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}


$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Python 가상환경을 찾을 수 없습니다: $Python"
}


Write-Step "Snapshot 관리자 페이지 업그레이드 적용"


$PatchProgram = @'
from __future__ import annotations


from pathlib import Path
import re
import shutil
from datetime import datetime


root = Path(r"__PROJECT_ROOT__")
force = __FORCE__


required = [
    root / "backend" / "app.py",
    root / "backend" / "snapshots" / "repository.py",
    root / "backend" / "snapshots" / "service.py",
    root / "admin" / "src" / "main.ts",
]
for path in required:
    if not path.exists():
        raise RuntimeError(f"필수 파일이 없습니다: {path}")


stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
backup_root = root / ".snapshot-admin-backup" / stamp
backup_root.mkdir(parents=True, exist_ok=True)
for path in required + [root / "admin" / "src" / "snapshots.ts"]:
    if path.exists():
        target = backup_root / path.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)




def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.replace("\n", "\r\n"), encoding="utf-8", newline="")
    print(f"WRITE {path.relative_to(root)}")




def replace_once(path: Path, old: str, new: str, label: str, already_marker: str | None = None) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0:
        if already_marker and already_marker in text:
            print(f"SKIP  {label} (already applied)")
            return
        raise RuntimeError(f"{label}: expected text not found in {path}")
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count} in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="")
    print(f"PATCH {label}")




def regex_once(path: Path, pattern: str, replacement: str, label: str, already_marker: str | None = None) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S | re.M)
    if count == 0:
        if already_marker and already_marker in text:
            print(f"SKIP  {label} (already applied)")
            return
        raise RuntimeError(f"{label}: regex target not found in {path}")
    path.write_text(updated, encoding="utf-8", newline="")
    print(f"PATCH {label}")




admin_snapshots_py = r'''from __future__ import annotations


import os
from collections.abc import Callable
from typing import Literal, Protocol


from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field


from .config import Settings
from .snapshots.contracts import (
    AccessPlan,
    LocatorRecord,
    RepositoryRecord,
    SnapshotFileResponse,
    SnapshotRecord,
    SnapshotTreeResponse,
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




class SnapshotAdminService(Protocol):
    @property
    def tenant_id(self) -> str: ...


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
'''


snapshots_ts = r'''export const SNAPSHOT_ROUTE = "/snapshots";


export const snapshotNavIcon = `
  <svg class="size-5" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path d="M7 4v4m0 0a2 2 0 1 0 0 4m0-4h8m2-4v4m0 0a2 2 0 1 1 0 4m0-4H9m-2 8v4m10-4v4M7 20h10" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" />
  </svg>`;


type SnapshotCounts = {
  repositories: number;
  snapshots: number;
  locators: number;
};


type SnapshotAdminStatus = {
  overall_status: "ready" | "degraded";
  feature_enabled: boolean;
  public_routes_exposed: boolean;
  token_configured: boolean;
  tenant_id: string;
  allowed_repositories: string[];
  database_ready: boolean;
  table_count: number;
  counts: SnapshotCounts;
  error: string | null;
};


type RepositoryRecord = {
  repository_id: string;
  provider: "github";
  provider_repository_id: string;
  repository_full_name: string;
  repository_url: string;
  default_branch: string;
  visibility: "public";
  created_at: string;
  updated_at: string;
};


type SnapshotRecord = {
  snapshot_id: string;
  repository_id: string;
  snapshot_type: "commit";
  commit_sha: string;
  tree_sha: string;
  fingerprint: string;
  verified_by: "github";
  verified_at: string;
  created_at: string;
};


type SnapshotOverview = {
  status: SnapshotAdminStatus;
  repositories: RepositoryRecord[];
  snapshots: SnapshotRecord[];
  page: number;
  page_size: number;
  total_repositories: number;
  total_snapshots: number;
  total_locators: number;
  has_more_repositories: boolean;
  has_more_snapshots: boolean;
};


type LocatorRecord = {
  locator_id: string;
  snapshot_id: string;
  provider: "github";
  access_mode: "backend-proxy";
  availability: "durable" | "unavailable";
  last_verified_at: string;
};


type AccessPlan = {
  snapshot_id: string;
  available: boolean;
  provider: "github";
  access_mode: "backend-proxy" | "unavailable";
  commit_sha: string;
  tree_sha: string;
  capabilities: Array<"commit.read" | "tree.read" | "file.read">;
  tree_endpoint: string | null;
  file_endpoint: string | null;
  reason: string | null;
};


type SnapshotDetail = {
  repository: RepositoryRecord;
  snapshot: SnapshotRecord;
  locator: LocatorRecord | null;
  access_plan: AccessPlan;
};


type TreeEntry = {
  path: string;
  entry_type: "blob" | "tree";
  object_sha: string;
  size: number | null;
  mode: string;
};


type TreeResponse = {
  snapshot_id: string;
  repository_id: string;
  commit_sha: string;
  tree_sha: string;
  entries: TreeEntry[];
  total: number;
};


type FileResponse = {
  snapshot_id: string;
  repository_id: string;
  commit_sha: string;
  tree_sha: string;
  path: string;
  blob_sha: string;
  size: number;
  encoding: "utf-8";
  content: string;
};


type ApiErrorBody = {
  detail?: string;
  error?: { message?: string };
};


let adminBaseUrl = "/admin-api";
let currentPage = 1;
const pageSize = 50;
let overview: SnapshotOverview | null = null;
let currentTree: TreeEntry[] = [];
let selectedSnapshotId: string | null = null;
let initialized = false;
let refreshTimer: number | null = null;


const htmlMap: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};


function escapeHtml(value: unknown): string {
  return String(value ?? "").replace(/[&<>"']/g, (character) => htmlMap[character]);
}


function formatDate(value: string | null | undefined): string {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString("ko-KR");
}


function shortSha(value: string): string {
  return value.slice(0, 12);
}


function setHtml(id: string, value: string): void {
  const element = document.getElementById(id);
  if (element) element.innerHTML = value;
}


function setText(id: string, value: string): void {
  const element = document.getElementById(id);
  if (element) element.textContent = value;
}


async function requestJson<T>(path: string): Promise<T> {
  const response = await fetch(`${adminBaseUrl}${path}`, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  const body = await response.json().catch(() => null) as T | ApiErrorBody | null;
  if (!response.ok) {
    const errorBody = body as ApiErrorBody | null;
    throw new Error(errorBody?.detail || errorBody?.error?.message || `HTTP ${response.status}`);
  }
  return body as T;
}


function statusTone(status: SnapshotAdminStatus): string {
  return status.database_ready
    ? "border-mint-300/20 bg-mint-400/7 text-mint-300"
    : "border-amber-300/20 bg-amber-300/7 text-amber-300";
}


function renderStatus(status: SnapshotAdminStatus): void {
  const publicState = status.public_routes_exposed ? "ON" : "OFF";
  const databaseState = status.database_ready ? "READY" : "DEGRADED";
  setHtml("snapshot-status-banner", `
    <div class="rounded-2xl border p-4 ${statusTone(status)}">
      <div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p class="text-sm font-semibold">Snapshot Control Plane · Public ${publicState}</p>
          <p class="mt-1 text-[11px] opacity-75">관리자 조회 경로는 내부 Proxy에서만 동작하며 운영 Token은 브라우저에 전달되지 않습니다.</p>
        </div>
        <span class="font-mono text-[10px]">DB ${databaseState} · TABLE ${status.table_count}/3</span>
      </div>
      ${status.error ? `<p class="mt-2 text-[10px]">${escapeHtml(status.error)}</p>` : ""}
    </div>`);
  setText("snapshot-count-repositories", status.counts.repositories.toLocaleString("ko-KR"));
  setText("snapshot-count-snapshots", status.counts.snapshots.toLocaleString("ko-KR"));
  setText("snapshot-count-locators", status.counts.locators.toLocaleString("ko-KR"));
  setText("snapshot-count-tables", `${status.table_count}/3`);
  setText("snapshot-runtime-tenant", status.tenant_id);
  setText("snapshot-runtime-token", status.token_configured ? "설정됨" : "미설정");
  setText("snapshot-runtime-allowlist", status.allowed_repositories.join(", ") || "없음");
}


function repositoryMarkup(repository: RepositoryRecord): string {
  return `
    <article class="rounded-xl border border-white/7 bg-white/2 p-3">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <p class="truncate text-[11px] font-semibold text-white/80">${escapeHtml(repository.repository_full_name)}</p>
          <p class="mt-1 break-all font-mono text-[9px] text-white/30">${escapeHtml(repository.repository_id)}</p>
        </div>
        <span class="rounded-md border border-mint-300/15 bg-mint-400/5 px-2 py-1 text-[9px] font-semibold text-mint-300">${escapeHtml(repository.visibility.toUpperCase())}</span>
      </div>
      <div class="mt-2 flex items-center justify-between text-[9px] text-white/35">
        <span>${escapeHtml(repository.default_branch)}</span>
        <span>${escapeHtml(formatDate(repository.updated_at))}</span>
      </div>
    </article>`;
}


function snapshotMarkup(snapshot: SnapshotRecord): string {
  const active = selectedSnapshotId === snapshot.snapshot_id;
  return `
    <button type="button" data-snapshot-id="${escapeHtml(snapshot.snapshot_id)}" class="w-full rounded-xl border p-3 text-left transition ${active ? "border-mint-300/30 bg-mint-400/8" : "border-white/7 bg-white/2 hover:border-white/15 hover:bg-white/4"}">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <p class="truncate text-[11px] font-semibold text-white/80">${escapeHtml(snapshot.snapshot_id)}</p>
          <p class="mt-1 font-mono text-[9px] text-white/30">commit ${escapeHtml(shortSha(snapshot.commit_sha))} · tree ${escapeHtml(shortSha(snapshot.tree_sha))}</p>
        </div>
        <span class="text-[9px] font-semibold text-mint-300">COMMIT</span>
      </div>
      <div class="mt-2 flex items-center justify-between text-[9px] text-white/35">
        <span>${escapeHtml(formatDate(snapshot.created_at))}</span>
        <span>${escapeHtml(snapshot.verified_by)}</span>
      </div>
    </button>`;
}


function renderOverview(value: SnapshotOverview): void {
  overview = value;
  renderStatus(value.status);
  setHtml(
    "snapshot-repository-list",
    value.repositories.map(repositoryMarkup).join("")
      || '<p class="py-8 text-center text-xs text-white/30">등록된 Repository가 없습니다.</p>',
  );
  setHtml(
    "snapshot-record-list",
    value.snapshots.map(snapshotMarkup).join("")
      || '<p class="py-8 text-center text-xs text-white/30">저장된 Snapshot이 없습니다.</p>',
  );
  setText("snapshot-page-label", `${value.page} 페이지 · ${value.total_snapshots}개 Snapshot`);
  const previous = document.querySelector<HTMLButtonElement>("#snapshot-page-previous");
  const next = document.querySelector<HTMLButtonElement>("#snapshot-page-next");
  if (previous) previous.disabled = value.page <= 1;
  if (next) next.disabled = !value.has_more_snapshots && !value.has_more_repositories;
}


function detailRow(label: string, value: string): string {
  return `<div class="grid gap-1 border-b border-white/5 py-2 sm:grid-cols-[150px_1fr]"><dt class="text-[9px] uppercase tracking-widest text-white/30">${escapeHtml(label)}</dt><dd class="break-all font-mono text-[10px] text-white/65">${escapeHtml(value)}</dd></div>`;
}


function renderDetail(value: SnapshotDetail): void {
  const locator = value.locator;
  const plan = value.access_plan;
  setHtml("snapshot-detail", `
    <div class="grid gap-4 xl:grid-cols-2">
      <dl>
        ${detailRow("Repository", value.repository.repository_full_name)}
        ${detailRow("Snapshot ID", value.snapshot.snapshot_id)}
        ${detailRow("Commit SHA", value.snapshot.commit_sha)}
        ${detailRow("Tree SHA", value.snapshot.tree_sha)}
        ${detailRow("Fingerprint", value.snapshot.fingerprint)}
        ${detailRow("Verified At", formatDate(value.snapshot.verified_at))}
      </dl>
      <dl>
        ${detailRow("Locator", locator?.locator_id || "없음")}
        ${detailRow("Provider", locator?.provider || plan.provider)}
        ${detailRow("Access Mode", locator?.access_mode || plan.access_mode)}
        ${detailRow("Availability", locator?.availability || (plan.available ? "available" : "unavailable"))}
        ${detailRow("Capabilities", plan.capabilities.join(", ") || "없음")}
        ${detailRow("Last Verified", formatDate(locator?.last_verified_at))}
      </dl>
    </div>
    ${plan.reason ? `<p class="mt-3 rounded-lg border border-amber-300/15 bg-amber-300/5 p-3 text-[10px] text-amber-300">${escapeHtml(plan.reason)}</p>` : ""}`);
}


function renderTree(): void {
  const filter = (document.querySelector<HTMLInputElement>("#snapshot-tree-filter")?.value || "").trim().toLowerCase();
  const entries = currentTree
    .filter((entry) => !filter || entry.path.toLowerCase().includes(filter))
    .sort((left, right) => left.path.localeCompare(right.path, "ko", { numeric: true }));
  setText("snapshot-tree-count", `${entries.length}/${currentTree.length}`);
  setHtml(
    "snapshot-tree-list",
    entries.map((entry) => {
      const clickable = entry.entry_type === "blob";
      return `
        <button type="button" ${clickable ? `data-file-path="${escapeHtml(entry.path)}"` : "disabled"} class="grid w-full grid-cols-[24px_minmax(0,1fr)_90px] items-center gap-2 border-b border-white/5 px-3 py-2 text-left text-[10px] transition ${clickable ? "hover:bg-white/4" : "cursor-default opacity-55"}">
          <span class="font-mono text-white/35">${entry.entry_type === "tree" ? "DIR" : "FILE"}</span>
          <span class="truncate text-white/65">${escapeHtml(entry.path)}</span>
          <span class="text-right font-mono text-[8px] text-white/25">${entry.size === null ? "-" : entry.size.toLocaleString("ko-KR")}</span>
        </button>`;
    }).join("") || '<p class="py-8 text-center text-xs text-white/30">표시할 Tree 항목이 없습니다.</p>',
  );
}


function renderFile(value: FileResponse): void {
  setText("snapshot-file-title", value.path);
  setText("snapshot-file-meta", `${value.encoding} · ${value.size.toLocaleString("ko-KR")} bytes · ${shortSha(value.blob_sha)}`);
  setHtml("snapshot-file-content", escapeHtml(value.content));
}


function showError(id: string, error: unknown): void {
  const message = error instanceof Error ? error.message : "알 수 없는 오류";
  setHtml(id, `<p class="rounded-xl border border-danger-300/15 bg-danger-300/5 p-3 text-[10px] text-danger-300">${escapeHtml(message)}</p>`);
}


async function loadOverview(): Promise<void> {
  const value = await requestJson<SnapshotOverview>(`/snapshots?page=${currentPage}&page_size=${pageSize}`);
  renderOverview(value);
}


async function refreshAll(): Promise<void> {
  const button = document.querySelector<HTMLButtonElement>("#snapshot-page-refresh");
  if (button) button.disabled = true;
  try {
    const [status, value] = await Promise.all([
      requestJson<SnapshotAdminStatus>("/snapshots/status"),
      requestJson<SnapshotOverview>(`/snapshots?page=${currentPage}&page_size=${pageSize}`),
    ]);
    renderStatus(status);
    renderOverview(value);
    setText("snapshot-last-refresh", `마지막 갱신 ${new Date().toLocaleString("ko-KR")}`);
  } catch (error) {
    showError("snapshot-status-banner", error);
  } finally {
    if (button) button.disabled = false;
  }
}


async function selectSnapshot(snapshotId: string): Promise<void> {
  selectedSnapshotId = snapshotId;
  if (overview) renderOverview(overview);
  setHtml("snapshot-detail", '<p class="text-xs text-white/35">Snapshot 상세를 조회하고 있습니다.</p>');
  setHtml("snapshot-tree-list", '<p class="py-8 text-center text-xs text-white/30">GitHub Tree를 조회하고 있습니다.</p>');
  setHtml("snapshot-file-content", "");
  setText("snapshot-file-title", "파일을 선택하세요");
  setText("snapshot-file-meta", "Tree에서 UTF-8 파일을 선택하면 내용이 표시됩니다.");
  const [detailResult, treeResult] = await Promise.allSettled([
    requestJson<SnapshotDetail>(`/snapshots/${encodeURIComponent(snapshotId)}`),
    requestJson<TreeResponse>(`/snapshots/${encodeURIComponent(snapshotId)}/tree`),
  ]);
  if (detailResult.status === "fulfilled") renderDetail(detailResult.value);
  else showError("snapshot-detail", detailResult.reason);
  if (treeResult.status === "fulfilled") {
    currentTree = treeResult.value.entries;
    renderTree();
  } else {
    currentTree = [];
    showError("snapshot-tree-list", treeResult.reason);
  }
}


async function loadFile(path: string): Promise<void> {
  if (!selectedSnapshotId) return;
  setText("snapshot-file-title", path);
  setText("snapshot-file-meta", "파일 내용을 조회하고 있습니다.");
  setHtml("snapshot-file-content", "");
  try {
    const value = await requestJson<FileResponse>(
      `/snapshots/${encodeURIComponent(selectedSnapshotId)}/file?path=${encodeURIComponent(path)}`,
    );
    renderFile(value);
  } catch (error) {
    showError("snapshot-file-content", error);
    setText("snapshot-file-meta", "조회 실패");
  }
}


export function snapshotAdminMarkup(): string {
  return `
    <div class="mx-auto max-w-[1440px] px-4 py-5 sm:px-6 lg:px-8">
      <section class="enter space-y-4" aria-label="Snapshot Explorer">
        <div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p class="font-mono text-[10px] uppercase tracking-[0.26em] text-mint-300/70">Immutable Source Control</p>
            <h1 class="mt-1 text-xl font-semibold text-white/90">Snapshot Explorer</h1>
            <p class="mt-1 max-w-3xl text-xs leading-5 text-white/38">Public GitHub Commit Snapshot의 저장 상태, Locator, Resolve, Tree와 UTF-8 파일 내용을 관리자 내부 경로에서 확인합니다.</p>
          </div>
          <div class="flex items-center gap-2">
            <span id="snapshot-last-refresh" class="font-mono text-[9px] text-white/28">갱신 준비 중</span>
            <button id="snapshot-page-refresh" type="button" class="rounded-xl bg-mint-400 px-3.5 py-2 text-xs font-bold text-ink-950 hover:bg-mint-300 disabled:opacity-50">새로고침</button>
          </div>
        </div>


        <div id="snapshot-status-banner"><p class="text-xs text-white/35">Snapshot 상태를 조회하고 있습니다.</p></div>


        <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          ${["repositories", "snapshots", "locators", "tables"].map((key) => {
            const labels: Record<string, string> = { repositories: "Repositories", snapshots: "Snapshots", locators: "Locators", tables: "DB Tables" };
            return `<article class="panel rounded-2xl p-4"><p class="text-[10px] uppercase tracking-widest text-white/30">${labels[key]}</p><p id="snapshot-count-${key}" class="mt-2 font-mono text-2xl font-semibold text-white/85">-</p></article>`;
          }).join("")}
        </div>


        <article class="panel rounded-2xl p-4">
          <div class="grid gap-2 text-[10px] text-white/45 md:grid-cols-3">
            <p>Tenant · <span id="snapshot-runtime-tenant" class="font-mono text-white/65">-</span></p>
            <p>운영 Token · <span id="snapshot-runtime-token" class="font-mono text-white/65">-</span></p>
            <p class="truncate">Allowlist · <span id="snapshot-runtime-allowlist" class="font-mono text-white/65">-</span></p>
          </div>
        </article>


        <div class="grid gap-4 xl:grid-cols-[minmax(260px,0.8fr)_minmax(320px,1fr)]">
          <article class="panel rounded-2xl p-4">
            <h2 class="text-sm font-semibold text-white/85">Repositories</h2>
            <div id="snapshot-repository-list" class="mt-3 max-h-[480px] space-y-2 overflow-y-auto"><p class="text-xs text-white/30">조회 중</p></div>
          </article>
          <article class="panel rounded-2xl p-4">
            <div class="flex items-center justify-between gap-3"><h2 class="text-sm font-semibold text-white/85">Commit Snapshots</h2><span id="snapshot-page-label" class="font-mono text-[9px] text-white/30">조회 중</span></div>
            <div id="snapshot-record-list" class="mt-3 max-h-[480px] space-y-2 overflow-y-auto"><p class="text-xs text-white/30">조회 중</p></div>
            <div class="mt-3 flex justify-end gap-2"><button id="snapshot-page-previous" type="button" class="rounded-lg border border-white/10 px-3 py-2 text-[10px] text-white/55 disabled:opacity-30">이전</button><button id="snapshot-page-next" type="button" class="rounded-lg border border-white/10 px-3 py-2 text-[10px] text-white/55 disabled:opacity-30">다음</button></div>
          </article>
        </div>


        <article class="panel rounded-2xl p-4">
          <h2 class="text-sm font-semibold text-white/85">Snapshot Identity · Locator · AccessPlan</h2>
          <div id="snapshot-detail" class="mt-3"><p class="text-xs text-white/30">Snapshot을 선택하세요.</p></div>
        </article>


        <div class="grid gap-4 xl:grid-cols-[minmax(360px,0.9fr)_minmax(0,1.4fr)]">
          <article class="panel min-h-[520px] rounded-2xl p-4">
            <div class="flex items-end justify-between gap-3"><label class="block min-w-0 flex-1"><span class="mb-1 block text-[9px] text-white/35">Tree 필터</span><input id="snapshot-tree-filter" class="playground-control w-full" type="search" placeholder="README, src/, compose…" /></label><span id="snapshot-tree-count" class="pb-2 font-mono text-[9px] text-white/30">0/0</span></div>
            <div id="snapshot-tree-list" class="mt-3 max-h-[430px] overflow-y-auto rounded-xl border border-white/7"><p class="py-8 text-center text-xs text-white/30">Snapshot을 선택하세요.</p></div>
          </article>
          <article class="panel min-h-[520px] min-w-0 rounded-2xl p-4">
            <div class="flex flex-col gap-1 border-b border-white/7 pb-3"><h2 id="snapshot-file-title" class="truncate text-sm font-semibold text-white/85">파일을 선택하세요</h2><p id="snapshot-file-meta" class="font-mono text-[9px] text-white/30">Tree에서 UTF-8 파일을 선택하면 내용이 표시됩니다.</p></div>
            <pre class="mt-3 max-h-[430px] overflow-auto rounded-xl border border-white/7 bg-black/20 p-4 text-[11px] leading-5 text-white/70"><code id="snapshot-file-content"></code></pre>
          </article>
        </div>
      </section>
    </div>`;
}


export function initializeSnapshotAdmin(value: string): void {
  adminBaseUrl = value.replace(/\/$/, "");
  if (initialized) return;
  initialized = true;
  document.getElementById("snapshot-page-refresh")?.addEventListener("click", () => void refreshAll());
  document.getElementById("snapshot-page-previous")?.addEventListener("click", () => {
    if (currentPage <= 1) return;
    currentPage -= 1;
    void loadOverview();
  });
  document.getElementById("snapshot-page-next")?.addEventListener("click", () => {
    if (!overview?.has_more_snapshots && !overview?.has_more_repositories) return;
    currentPage += 1;
    void loadOverview();
  });
  document.getElementById("snapshot-record-list")?.addEventListener("click", (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-snapshot-id]");
    const snapshotId = button?.dataset.snapshotId;
    if (snapshotId) void selectSnapshot(snapshotId);
  });
  document.getElementById("snapshot-tree-list")?.addEventListener("click", (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-file-path]");
    const path = button?.dataset.filePath;
    if (path) void loadFile(path);
  });
  document.getElementById("snapshot-tree-filter")?.addEventListener("input", renderTree);
  void refreshAll();
  refreshTimer = window.setInterval(() => void refreshAll(), 30_000);
  window.addEventListener("pagehide", () => {
    if (refreshTimer !== null) window.clearInterval(refreshTimer);
  }, { once: true });
}
'''


admin_api_test_py = r'''from __future__ import annotations


from datetime import datetime, timezone
from types import SimpleNamespace


from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient


from backend.admin_snapshots import create_admin_snapshot_router
from backend.snapshots.contracts import (
    AccessPlan,
    LocatorRecord,
    RepositoryRecord,
    SnapshotFileResponse,
    SnapshotRecord,
    SnapshotTreeResponse,
    TreeEntry,
)
from backend.snapshots.service import GithubSnapshotServiceError




NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)
REPOSITORY = RepositoryRecord(
    repository_id="repo_test",
    provider_repository_id="1306244300",
    repository_full_name="h5vision/api_test",
    repository_url="https://github.com/h5vision/api_test.git",
    default_branch="main",
    created_at=NOW,
    updated_at=NOW,
)
SNAPSHOT = SnapshotRecord(
    snapshot_id="snap_test",
    repository_id=REPOSITORY.repository_id,
    commit_sha="a" * 40,
    tree_sha="b" * 40,
    fingerprint="c" * 64,
    verified_at=NOW,
    created_at=NOW,
)
LOCATOR = LocatorRecord(
    locator_id="loc_test",
    snapshot_id=SNAPSHOT.snapshot_id,
    availability="durable",
    last_verified_at=NOW,
)
PLAN = AccessPlan(
    snapshot_id=SNAPSHOT.snapshot_id,
    available=True,
    access_mode="backend-proxy",
    commit_sha=SNAPSHOT.commit_sha,
    tree_sha=SNAPSHOT.tree_sha,
    capabilities=["commit.read", "tree.read", "file.read"],
    tree_endpoint=f"/v1/snapshot-control/snapshots/{SNAPSHOT.snapshot_id}/tree",
    file_endpoint=f"/v1/snapshot-control/snapshots/{SNAPSHOT.snapshot_id}/file",
)




class FakeSnapshotService:
    tenant_id = "vision-default"


    def admin_status(self):
        return {"table_count": 3, "repositories": 1, "snapshots": 1, "locators": 1}


    def list_repositories(self, *, limit=100, offset=0):
        return [REPOSITORY] if offset == 0 else []


    def list_snapshots_for_tenant(self, *, limit=100, offset=0):
        return [SNAPSHOT] if offset == 0 else []


    def get_repository(self, repository_id):
        assert repository_id == REPOSITORY.repository_id
        return REPOSITORY


    def get_snapshot(self, snapshot_id):
        assert snapshot_id == SNAPSHOT.snapshot_id
        return SNAPSHOT


    def get_locator(self, snapshot_id):
        assert snapshot_id == SNAPSHOT.snapshot_id
        return LOCATOR


    def resolve(self, snapshot_id):
        assert snapshot_id == SNAPSHOT.snapshot_id
        return PLAN


    def tree(self, snapshot_id):
        assert snapshot_id == SNAPSHOT.snapshot_id
        return SnapshotTreeResponse(
            snapshot_id=SNAPSHOT.snapshot_id,
            repository_id=REPOSITORY.repository_id,
            commit_sha=SNAPSHOT.commit_sha,
            tree_sha=SNAPSHOT.tree_sha,
            entries=[TreeEntry(path="README.md", entry_type="blob", object_sha="d" * 40, size=12, mode="100644")],
            total=1,
        )


    def file(self, snapshot_id, path):
        assert snapshot_id == SNAPSHOT.snapshot_id
        return SnapshotFileResponse(
            snapshot_id=SNAPSHOT.snapshot_id,
            repository_id=REPOSITORY.repository_id,
            commit_sha=SNAPSHOT.commit_sha,
            tree_sha=SNAPSHOT.tree_sha,
            path=path,
            blob_sha="d" * 40,
            size=12,
            content="# API test\n",
        )




class FailingSnapshotService(FakeSnapshotService):
    def admin_status(self):
        raise GithubSnapshotServiceError("Snapshot storage is unavailable", status_code=503)




def build_client(service):
    app = FastAPI()


    def require_proxy(request: Request):
        if request.headers.get("x-vision-admin-proxy") != "dashboard-internal":
            raise HTTPException(status_code=403, detail="dashboard only")


    app.include_router(
        create_admin_snapshot_router(
            SimpleNamespace(),
            require_proxy,
            service_factory=lambda: service,
        )
    )
    return TestClient(app)




def admin_headers():
    return {"X-Vision-Admin-Proxy": "dashboard-internal"}




def test_admin_snapshot_routes_require_internal_proxy():
    response = build_client(FakeSnapshotService()).get("/v1/admin/snapshots/status")
    assert response.status_code == 403




def test_admin_snapshot_overview_has_exact_counts_and_no_secret():
    response = build_client(FakeSnapshotService()).get(
        "/v1/admin/snapshots?page=1&page_size=50",
        headers=admin_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total_repositories"] == 1
    assert body["total_snapshots"] == 1
    assert body["total_locators"] == 1
    assert body["repositories"][0]["repository_full_name"] == "h5vision/api_test"
    assert body["snapshots"][0]["snapshot_id"] == "snap_test"
    assert "SNAPSHOT_MVP_TOKEN" not in response.text




def test_admin_snapshot_detail_tree_and_file():
    client = build_client(FakeSnapshotService())
    detail = client.get("/v1/admin/snapshots/snap_test", headers=admin_headers())
    tree = client.get("/v1/admin/snapshots/snap_test/tree", headers=admin_headers())
    file_response = client.get(
        "/v1/admin/snapshots/snap_test/file",
        params={"path": "README.md"},
        headers=admin_headers(),
    )
    assert detail.status_code == 200
    assert detail.json()["locator"]["availability"] == "durable"
    assert detail.json()["access_plan"]["available"] is True
    assert tree.status_code == 200
    assert tree.json()["entries"][0]["path"] == "README.md"
    assert file_response.status_code == 200
    assert file_response.json()["content"] == "# API test\n"




def test_admin_snapshot_storage_failure_uses_503_for_listing():
    response = build_client(FailingSnapshotService()).get(
        "/v1/admin/snapshots",
        headers=admin_headers(),
    )
    assert response.status_code == 503
'''


frontend_contract_test_py = r'''from pathlib import Path




ROOT = Path(__file__).resolve().parents[1]




def test_snapshot_frontend_is_a_real_route_module():
    main = (ROOT / "admin" / "src" / "main.ts").read_text(encoding="utf-8")
    snapshots = (ROOT / "admin" / "src" / "snapshots.ts").read_text(encoding="utf-8")


    assert 'from "./snapshots"' in main
    assert "SNAPSHOT_ROUTE" in main
    assert "initializeSnapshotAdmin(adminApiBaseUrl)" in main
    assert "snapshotAdminMarkup()" in main
    assert 'export const SNAPSHOT_ROUTE = "/snapshots"' in snapshots
    assert "/snapshots/status" in snapshots
    assert "/tree" in snapshots
    assert "/file?path=" in snapshots
    assert "X-Vision-Snapshot-Token" not in snapshots
    assert "SNAPSHOT_MVP_TOKEN" not in snapshots
    assert "Snapshot 저장 기록" not in main
'''


write_text(root / "backend" / "admin_snapshots.py", admin_snapshots_py)
write_text(root / "admin" / "src" / "snapshots.ts", snapshots_ts)
write_text(root / "tests" / "test_admin_snapshot_api.py", admin_api_test_py)
write_text(root / "tests" / "test_admin_snapshot_frontend_contract.py", frontend_contract_test_py)


repository_path = root / "backend" / "snapshots" / "repository.py"
regex_once(
    repository_path,
    r'''    def list_repositories\(\n        self,\n        tenant_id: str,\n        \*,\n        limit: int = 100,\n    \) -> list\[dict\[str, Any\]\]:.*?        return list\(rows\)\n\n\n    def register_verified_snapshot''',
    '''    def list_repositories(\n        self,\n        tenant_id: str,\n        *,\n        limit: int = 100,\n        offset: int = 0,\n    ) -> list[dict[str, Any]]:\n        bounded_limit = max(1, min(limit, 500))\n        bounded_offset = max(0, offset)\n        try:\n            with self._connect() as connection:\n                rows = connection.execute(\n                    """\n                    SELECT *\n                    FROM snapshot_mvp_repositories\n                    WHERE tenant_id = %(tenant_id)s\n                      AND provider = 'github'\n                      AND visibility = 'public'\n                    ORDER BY created_at DESC\n                    LIMIT %(limit)s\n                    OFFSET %(offset)s\n                    """,\n                    {\n                        "tenant_id": tenant_id,\n                        "limit": bounded_limit,\n                        "offset": bounded_offset,\n                    },\n                ).fetchall()\n        except psycopg.Error as exc:\n            raise SnapshotRepositoryError(\n                f"Failed to list GitHub repositories: {exc}"\n            ) from exc\n        return list(rows)\n\n\n    def admin_status(self, tenant_id: str) -> dict[str, int]:\n        try:\n            with self._connect() as connection:\n                table_row = connection.execute(\n                    """\n                    SELECT\n                        (CASE WHEN to_regclass('public.snapshot_mvp_repositories') IS NOT NULL THEN 1 ELSE 0 END\n                       + CASE WHEN to_regclass('public.snapshot_mvp_snapshots') IS NOT NULL THEN 1 ELSE 0 END\n                       + CASE WHEN to_regclass('public.snapshot_mvp_locators') IS NOT NULL THEN 1 ELSE 0 END)\n                        AS table_count\n                    """\n                ).fetchone()\n                table_count = int((table_row or {}).get("table_count") or 0)\n                if table_count != 3:\n                    return {\n                        "table_count": table_count,\n                        "repositories": 0,\n                        "snapshots": 0,\n                        "locators": 0,\n                    }\n                count_row = connection.execute(\n                    """\n                    SELECT\n                        (SELECT COUNT(*) FROM snapshot_mvp_repositories\n                         WHERE tenant_id = %(tenant_id)s\n                           AND provider = 'github'\n                           AND visibility = 'public') AS repositories,\n                        (SELECT COUNT(*) FROM snapshot_mvp_snapshots\n                         WHERE tenant_id = %(tenant_id)s\n                           AND snapshot_type = 'commit') AS snapshots,\n                        (SELECT COUNT(*) FROM snapshot_mvp_locators\n                         WHERE tenant_id = %(tenant_id)s\n                           AND provider = 'github'\n                           AND access_mode = 'backend-proxy') AS locators\n                    """,\n                    {"tenant_id": tenant_id},\n                ).fetchone()\n        except psycopg.Error as exc:\n            raise SnapshotRepositoryError(\n                f"Failed to inspect Snapshot storage: {exc}"\n            ) from exc\n        return {\n            "table_count": table_count,\n            "repositories": int((count_row or {}).get("repositories") or 0),\n            "snapshots": int((count_row or {}).get("snapshots") or 0),\n            "locators": int((count_row or {}).get("locators") or 0),\n        }\n\n\n    def register_verified_snapshot''',
    "repository list pagination and exact status",
    already_marker="def admin_status(self, tenant_id: str)",
)
regex_once(
    repository_path,
    r'''    def list_snapshots_for_tenant\(\n        self,\n        tenant_id: str,\n        \*,\n        limit: int = 100,\n    \) -> list\[dict\[str, Any\]\]:.*?        return list\(rows\)\s*\Z''',
    '''    def list_snapshots_for_tenant(\n        self,\n        tenant_id: str,\n        *,\n        limit: int = 100,\n        offset: int = 0,\n    ) -> list[dict[str, Any]]:\n        bounded_limit = max(1, min(limit, 500))\n        bounded_offset = max(0, offset)\n        try:\n            with self._connect() as connection:\n                rows = connection.execute(\n                    """\n                    SELECT *\n                    FROM snapshot_mvp_snapshots\n                    WHERE tenant_id = %(tenant_id)s\n                      AND snapshot_type = 'commit'\n                    ORDER BY created_at DESC\n                    LIMIT %(limit)s\n                    OFFSET %(offset)s\n                    """,\n                    {\n                        "tenant_id": tenant_id,\n                        "limit": bounded_limit,\n                        "offset": bounded_offset,\n                    },\n                ).fetchall()\n        except psycopg.Error as exc:\n            raise SnapshotRepositoryError(\n                f"Failed to list snapshots for tenant: {exc}"\n            ) from exc\n        return list(rows)\n''',
    "snapshot list pagination",
    already_marker="OFFSET %(offset)s",
)


service_path = root / "backend" / "snapshots" / "service.py"
regex_once(
    service_path,
    r'''    def list_repositories\(self, \*, limit: int = 100\) -> list\[RepositoryRecord\]:.*?    def create_snapshot\(''',
    '''    def list_repositories(\n        self,\n        *,\n        limit: int = 100,\n        offset: int = 0,\n    ) -> list[RepositoryRecord]:\n        try:\n            rows = self._repository.list_repositories(\n                self.tenant_id,\n                limit=limit,\n                offset=offset,\n            )\n        except SnapshotRepositoryError as exc:\n            logger.exception("GitHub Snapshot repository listing failed")\n            raise GithubSnapshotServiceError(\n                "Snapshot storage is unavailable",\n                status_code=503,\n            ) from exc\n        return [self._repository_record(row) for row in rows]\n\n\n    def list_snapshots_for_tenant(\n        self,\n        *,\n        limit: int = 100,\n        offset: int = 0,\n    ) -> list[SnapshotRecord]:\n        try:\n            rows = self._repository.list_snapshots_for_tenant(\n                self.tenant_id,\n                limit=limit,\n                offset=offset,\n            )\n        except SnapshotRepositoryError as exc:\n            logger.exception("GitHub Snapshot tenant listing failed")\n            raise GithubSnapshotServiceError(\n                "Snapshot storage is unavailable",\n                status_code=503,\n            ) from exc\n        return [self._snapshot_record(row) for row in rows]\n\n\n    def admin_status(self) -> dict[str, int]:\n        try:\n            return self._repository.admin_status(self.tenant_id)\n        except SnapshotRepositoryError as exc:\n            logger.exception("GitHub Snapshot admin status failed")\n            raise GithubSnapshotServiceError(\n                "Snapshot storage is unavailable",\n                status_code=503,\n            ) from exc\n\n\n    def create_snapshot(''',
    "service pagination and admin status",
    already_marker="def admin_status(self) -> dict[str, int]",
)
regex_once(
    service_path,
    r'''    def get_snapshot\(self, snapshot_id: str\) -> SnapshotRecord:.*?        return self\._snapshot_record\(row\)\n\n\n    def list_snapshots\(''',
    '''    def get_snapshot(self, snapshot_id: str) -> SnapshotRecord:\n        try:\n            row = self._repository.get_snapshot(self.tenant_id, snapshot_id)\n        except SnapshotRepositoryError as exc:\n            logger.exception("GitHub Snapshot lookup failed")\n            raise GithubSnapshotServiceError(\n                "Snapshot storage is unavailable",\n                status_code=503,\n            ) from exc\n        if row is None:\n            raise GithubSnapshotServiceError("Snapshot was not found", status_code=404)\n        return self._snapshot_record(row)\n\n\n    def get_locator(self, snapshot_id: str) -> LocatorRecord | None:\n        self.get_snapshot(snapshot_id)\n        try:\n            row = self._repository.get_github_locator(self.tenant_id, snapshot_id)\n        except SnapshotRepositoryError as exc:\n            logger.exception("GitHub Snapshot locator lookup failed")\n            raise GithubSnapshotServiceError(\n                "Snapshot storage is unavailable",\n                status_code=503,\n            ) from exc\n        return self._locator_record(row)\n\n\n    def list_snapshots(''',
    "public locator lookup",
    already_marker="def get_locator(self, snapshot_id: str)",
)


app_path = root / "backend" / "app.py"
replace_once(
    app_path,
    "from .snapshots.service import GithubSnapshotService, GithubSnapshotServiceError\n",
    "from .admin_snapshots import create_admin_snapshot_router\n",
    "app snapshot admin import",
    already_marker="from .admin_snapshots import create_admin_snapshot_router",
)
regex_once(
    app_path,
    r'''@app\.get\(\n    "/v1/admin/snapshots",.*?\n\n@app\.get\(\n    "/v1/admin/frontend-clients",''',
    '''app.include_router(create_admin_snapshot_router(settings, _require_admin_proxy))\n\n\n@app.get(\n    "/v1/admin/frontend-clients",''',
    "replace prototype admin endpoint with router",
    already_marker="app.include_router(create_admin_snapshot_router(settings, _require_admin_proxy))",
)


main_path = root / "admin" / "src" / "main.ts"
replace_once(
    main_path,
    'import { playgroundMarkup, startPlayground } from "./playground";\n',
    'import { playgroundMarkup, startPlayground } from "./playground";\nimport {\n  SNAPSHOT_ROUTE,\n  initializeSnapshotAdmin,\n  snapshotAdminMarkup,\n  snapshotNavIcon,\n} from "./snapshots";\n',
    "main snapshot module import",
    already_marker='from "./snapshots"',
)
regex_once(
    main_path,
    r'''type SnapshotRecord = \{.*?type SnapshotAdminResponse = \{.*?\};\n\n''',
    "",
    "remove inline snapshot types",
    already_marker="type SnapshotAdminStatus =",
)
replace_once(
    main_path,
    'const isPlayground = window.location.pathname.startsWith("/playground");\nconst isSystemStatus = window.location.pathname.startsWith(SYSTEM_STATUS_ROUTE);\nconst isOverview = !isPlayground && !isSystemStatus;\n',
    'const isPlayground = window.location.pathname.startsWith("/playground");\nconst isSystemStatus = window.location.pathname.startsWith(SYSTEM_STATUS_ROUTE);\nconst isSnapshots = window.location.pathname.startsWith(SNAPSHOT_ROUTE);\nconst isOverview = !isPlayground && !isSystemStatus && !isSnapshots;\n',
    "main snapshot route",
    already_marker="const isSnapshots =",
)
replace_once(
    main_path,
    '        ${navItem("시스템 상태", icons.pulse, SYSTEM_STATUS_ROUTE, isSystemStatus)}\n        ${navItem("sLLM Playground", icons.cube, "/playground", isPlayground)}\n',
    '        ${navItem("시스템 상태", icons.pulse, SYSTEM_STATUS_ROUTE, isSystemStatus)}\n        ${navItem("Snapshot Explorer", snapshotNavIcon, SNAPSHOT_ROUTE, isSnapshots)}\n        ${navItem("sLLM Playground", icons.cube, "/playground", isPlayground)}\n',
    "main snapshot navigation",
    already_marker='navItem("Snapshot Explorer"',
)
regex_once(
    main_path,
    r'''\n          <article class="panel rounded-2xl p-4 md:col-span-2 xl:col-span-3">\n            <div class="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">\n              <div>\n                <h2 class="text-base font-semibold text-white/90">Snapshot 저장 기록</h2>.*?\n          </article>\n''',
    "\n",
    "remove overview snapshot prototype card",
    already_marker='${snapshotAdminMarkup()}',
)
replace_once(
    main_path,
    '      <div class="${isSystemStatus ? "" : "hidden"}">${systemStatusMarkup(apiBaseUrl, icons)}</div>\n      <div class="${isPlayground ? "" : "hidden"}">${playgroundMarkup()}</div>\n',
    '      <div class="${isSystemStatus ? "" : "hidden"}">${systemStatusMarkup(apiBaseUrl, icons)}</div>\n      <div class="${isSnapshots ? "" : "hidden"}">${snapshotAdminMarkup()}</div>\n      <div class="${isPlayground ? "" : "hidden"}">${playgroundMarkup()}</div>\n',
    "main snapshot page markup",
    already_marker='${snapshotAdminMarkup()}',
)
regex_once(
    main_path,
    r'''function snapshotAdminMarkup\(data: SnapshotAdminResponse\): string \{.*?\nlet frontendClients: FrontendClientRecord\[\] = \[\];''',
    'let frontendClients: FrontendClientRecord[] = [];',
    "remove inline snapshot implementation",
    already_marker="export function snapshotAdminMarkup",
)
main_text = main_path.read_text(encoding="utf-8")
main_text = main_text.replace("        loadEmbeddingArtifacts(),\n        loadSnapshotAdminData(),\n", "        loadEmbeddingArtifacts(),\n")
main_path.write_text(main_text, encoding="utf-8", newline="")
print("PATCH main overview refresh list")
replace_once(
    main_path,
    '  } else {\n    if (isSystemStatus) initializeSystemStatus(adminApiBaseUrl);\n    void refreshDashboard();\n  }\n',
    '  } else if (isSnapshots) {\n    initializeSnapshotAdmin(adminApiBaseUrl);\n    void refreshDashboard();\n  } else {\n    if (isSystemStatus) initializeSystemStatus(adminApiBaseUrl);\n    void refreshDashboard();\n  }\n',
    "main snapshot initialization",
    already_marker="initializeSnapshotAdmin(adminApiBaseUrl)",
)


print(f"BACKUP {backup_root}")
print("Snapshot 관리자 페이지 업그레이드 패치 완료")
'@


$PatchProgram = $PatchProgram.Replace("__PROJECT_ROOT__", $ProjectRoot.Replace("\", "\\"))
$PatchProgram = $PatchProgram.Replace("__FORCE__", $(if ($Force) { "True" } else { "False" }))
$PatchProgram | & $Python -
if ($LASTEXITCODE -ne 0) {
    throw "Snapshot 관리자 페이지 패치가 실패했습니다. .snapshot-admin-backup 폴더에서 복구할 수 있습니다."
}


Write-Step "정적 컴파일 확인"
& $Python -m compileall (Join-Path $ProjectRoot "backend")
if ($LASTEXITCODE -ne 0) { throw "Backend compileall 실패" }


Write-Host "`n적용이 완료됐습니다. 다음으로 verify_snapshot_admin.ps1을 실행하세요." -ForegroundColor Green
