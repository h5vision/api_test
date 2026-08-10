export const SNAPSHOT_ROUTE = "/snapshots";


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


type SnapshotListResponse = {
  repository_id: string;
  snapshots: SnapshotRecord[];
  total: number;
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


type SnapshotImportResponse = {
  repository: RepositoryRecord;
  snapshot: SnapshotRecord;
  deduplicated: boolean;
  resolved_ref: string;
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
let repositorySnapshots: SnapshotRecord[] = [];
let currentTree: TreeEntry[] = [];
let selectedRepositoryId: string | null = null;
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


async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${adminBaseUrl}${path}`, {
    ...init,
    headers,
    cache: init.cache ?? "no-store",
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
          <p class="text-sm font-semibold">Snapshot Control Plane \u00B7 Public ${publicState}</p>
          <p class="mt-1 text-[11px] opacity-75">\uAD00\uB9AC\uC790 \uC870\uD68C \uACBD\uB85C\uB294 \uB0B4\uBD80 Proxy\uC5D0\uC11C\uB9CC \uB3D9\uC791\uD558\uBA70 \uC6B4\uC601 Token\uC740 \uBE0C\uB77C\uC6B0\uC800\uC5D0 \uC804\uB2EC\uD558\uC9C0 \uC54A\uC2B5\uB2C8\uB2E4.</p>
        </div>
        <span class="font-mono text-[10px]">DB ${databaseState} \u00B7 TABLE ${status.table_count}/3</span>
      </div>
      ${status.error ? `<p class="mt-2 text-[10px]">${escapeHtml(status.error)}</p>` : ""}
    </div>`);
  setText("snapshot-count-repositories", status.counts.repositories.toLocaleString("ko-KR"));
  setText("snapshot-count-snapshots", status.counts.snapshots.toLocaleString("ko-KR"));
  setText("snapshot-count-locators", status.counts.locators.toLocaleString("ko-KR"));
  setText("snapshot-count-tables", `${status.table_count}/3`);
  setText("snapshot-runtime-tenant", status.tenant_id);
  setText("snapshot-runtime-token", status.token_configured ? "\uC124\uC815\uB428" : "\uBBF8\uC124\uC815");
  setText("snapshot-runtime-allowlist", status.allowed_repositories.join(", ") || "\uC5C6\uC74C");
}


function repositoryMarkup(repository: RepositoryRecord): string {
  const active = selectedRepositoryId === repository.repository_id;
  return `
    <button type="button" data-repository-id="${escapeHtml(repository.repository_id)}" class="w-full rounded-xl border p-3 text-left transition ${active ? "border-mint-300/30 bg-mint-400/8" : "border-white/7 bg-white/2 hover:border-white/15 hover:bg-white/4"}">
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
    </button>`;
}



function snapshotMarkup(snapshot: SnapshotRecord): string {
  const active = selectedSnapshotId === snapshot.snapshot_id;
  return `
    <button type="button" data-snapshot-id="${escapeHtml(snapshot.snapshot_id)}" class="w-full rounded-xl border p-3 text-left transition ${active ? "border-mint-300/30 bg-mint-400/8" : "border-white/7 bg-white/2 hover:border-white/15 hover:bg-white/4"}">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <p class="truncate text-[11px] font-semibold text-white/80">${escapeHtml(snapshot.snapshot_id)}</p>
          <p class="mt-1 font-mono text-[9px] text-white/30">commit ${escapeHtml(shortSha(snapshot.commit_sha))} \u00B7 tree ${escapeHtml(shortSha(snapshot.tree_sha))}</p>
        </div>
        <span class="text-[9px] font-semibold text-mint-300">COMMIT</span>
      </div>
      <div class="mt-2 flex items-center justify-between text-[9px] text-white/35">
        <span>${escapeHtml(formatDate(snapshot.created_at))}</span>
        <span>${escapeHtml(snapshot.verified_by)}</span>
      </div>
    </button>`;
}


function selectedRepository(): RepositoryRecord | null {
  return overview?.repositories.find((repository) => repository.repository_id === selectedRepositoryId) || null;
}


function renderSnapshotRecords(): void {
  const repository = selectedRepository();
  const snapshots = selectedRepositoryId ? repositorySnapshots : (overview?.snapshots || []);
  setText("snapshot-list-title", repository ? `${repository.repository_full_name} Snapshots` : "전체 Commit Snapshots");
  setHtml(
    "snapshot-record-list",
    snapshots.map(snapshotMarkup).join("")
      || `<p class="py-8 text-center text-xs text-white/30">${repository ? "이 Repository에 저장된 Snapshot이 없습니다." : "저장된 Snapshot이 없습니다."}</p>`,
  );
  if (repository) {
    setText("snapshot-page-label", `${snapshots.length}개 Snapshot · ${repository.default_branch}`);
  } else if (overview) {
    setText("snapshot-page-label", `${overview.page} 페이지 · ${overview.total_snapshots}개 Snapshot`);
  }
  const previous = document.querySelector<HTMLButtonElement>("#snapshot-page-previous");
  const next = document.querySelector<HTMLButtonElement>("#snapshot-page-next");
  if (previous) previous.disabled = selectedRepositoryId !== null || !overview || overview.page <= 1;
  if (next) next.disabled = selectedRepositoryId !== null || !overview || (!overview.has_more_snapshots && !overview.has_more_repositories);
}


function renderOverview(value: SnapshotOverview): void {
  overview = value;
  renderStatus(value.status);
  setHtml(
    "snapshot-repository-list",
    value.repositories.map(repositoryMarkup).join("")
      || '<p class="py-8 text-center text-xs text-white/30">등록된 Repository가 없습니다.</p>',
  );
  renderSnapshotRecords();
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
        ${detailRow("Locator", locator?.locator_id || "\uC5C6\uC74C")}
        ${detailRow("Provider", locator?.provider || plan.provider)}
        ${detailRow("Access Mode", locator?.access_mode || plan.access_mode)}
        ${detailRow("Availability", locator?.availability || (plan.available ? "available" : "unavailable"))}
        ${detailRow("Capabilities", plan.capabilities.join(", ") || "\uC5C6\uC74C")}
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
    }).join("") || '<p class="py-8 text-center text-xs text-white/30">\uD45C\uC2DC\uD560 Tree \uD56D\uBAA9\uC774 \uC5C6\uC2B5\uB2C8\uB2E4.</p>',
  );
}


function renderFile(value: FileResponse): void {
  setText("snapshot-file-title", value.path);
  setText("snapshot-file-meta", `${value.encoding} \u00B7 ${value.size.toLocaleString("ko-KR")} bytes \u00B7 ${shortSha(value.blob_sha)}`);
  setHtml("snapshot-file-content", escapeHtml(value.content));
}


function showError(id: string, error: unknown): void {
  const message = error instanceof Error ? error.message : "\uC54C \uC218 \uC5C6\uB294 \uC624\uB958";
  setHtml(id, `<p class="rounded-xl border border-danger-300/15 bg-danger-300/5 p-3 text-[10px] text-danger-300">${escapeHtml(message)}</p>`);
}


async function importRepositorySnapshot(event: SubmitEvent): Promise<void> {
  event.preventDefault();
  const repositoryInput = document.querySelector<HTMLInputElement>("#snapshot-import-url");
  const refInput = document.querySelector<HTMLInputElement>("#snapshot-import-ref");
  const button = document.querySelector<HTMLButtonElement>("#snapshot-import-submit");
  const repositoryUrl = repositoryInput?.value.trim() || "";
  const ref = refInput?.value.trim() || "";


  if (!repositoryUrl) {
    setText("snapshot-import-result", "\uC811\uC18D\uD560 GitHub Repository URL\uC744 \uC785\uB825\uD558\uC138\uC694.");
    repositoryInput?.focus();
    return;
  }


  if (button) button.disabled = true;
  setText("snapshot-import-result", "GitHub Repository \uC815\uBCF4\uC640 Commit Snapshot\uC744 \uD655\uC778\uD558\uACE0 \uC788\uC2B5\uB2C8\uB2E4.");
  try {
    const value = await requestJson<SnapshotImportResponse>("/snapshots/import", {
      method: "POST",
      body: JSON.stringify({
        repository_url: repositoryUrl,
        ref: ref || null,
      }),
    });
    currentPage = 1;
    await refreshAll();
    await selectRepository(value.repository.repository_id);
    const state = value.deduplicated
      ? "\uAE30\uC874 Snapshot \uC7AC\uC0AC\uC6A9"
      : "\uC0C8 Snapshot \uB4F1\uB85D";
    setHtml(
      "snapshot-import-result",
      `<span class="text-mint-300">${escapeHtml(value.repository.repository_full_name)}</span> \u00B7 ${escapeHtml(value.resolved_ref)} \u00B7 ${escapeHtml(shortSha(value.snapshot.commit_sha))} \u00B7 ${state}`,
    );
    if (refInput) refInput.value = "";
    await selectSnapshot(value.snapshot.snapshot_id);
  } catch (error) {
    showError("snapshot-import-result", error);
  } finally {
    if (button) button.disabled = false;
  }
}




async function loadOverview(): Promise<void> {
  const value = await requestJson<SnapshotOverview>(`/snapshots?page=${currentPage}&page_size=${pageSize}`);
  renderOverview(value);
}


async function refreshAll(): Promise<void> {
  const button = document.querySelector<HTMLButtonElement>("#snapshot-page-refresh");
  if (button) button.disabled = true;
  try {
    const repositoryRequest = selectedRepositoryId
      ? requestJson<SnapshotListResponse>(`/snapshots/repositories/${encodeURIComponent(selectedRepositoryId)}/snapshots?limit=500`)
      : Promise.resolve(null);
    const [status, value, repositoryValue] = await Promise.all([
      requestJson<SnapshotAdminStatus>("/snapshots/status"),
      requestJson<SnapshotOverview>(`/snapshots?page=${currentPage}&page_size=${pageSize}`),
      repositoryRequest,
    ]);
    if (repositoryValue) repositorySnapshots = repositoryValue.snapshots;
    renderStatus(status);
    renderOverview(value);
    setText("snapshot-last-refresh", `\uB9C8\uC9C0\uB9C9 \uAC31\uC2E0 ${new Date().toLocaleString("ko-KR")}`);
  } catch (error) {
    showError("snapshot-status-banner", error);
  } finally {
    if (button) button.disabled = false;
  }
}


function clearSnapshotSelection(): void {
  selectedSnapshotId = null;
  currentTree = [];
  setHtml("snapshot-detail", '<p class="text-xs text-white/30">Snapshot을 선택하세요.</p>');
  setHtml("snapshot-tree-list", '<p class="py-8 text-center text-xs text-white/30">Snapshot을 선택하세요.</p>');
  setText("snapshot-tree-count", "0/0");
  setHtml("snapshot-file-content", "");
  setText("snapshot-file-title", "파일을 선택하세요");
  setText("snapshot-file-meta", "Tree에서 UTF-8 파일을 선택하면 내용을 표시합니다.");
}


async function selectRepository(repositoryId: string): Promise<void> {
  selectedRepositoryId = repositoryId;
  repositorySnapshots = [];
  clearSnapshotSelection();
  if (overview) renderOverview(overview);
  setHtml("snapshot-record-list", '<p class="py-8 text-center text-xs text-white/30">Repository Snapshot을 조회하고 있습니다.</p>');
  try {
    const value = await requestJson<SnapshotListResponse>(
      `/snapshots/repositories/${encodeURIComponent(repositoryId)}/snapshots?limit=500`,
    );
    repositorySnapshots = value.snapshots;
    renderSnapshotRecords();
  } catch (error) {
    repositorySnapshots = [];
    showError("snapshot-record-list", error);
  }
}


function showAllSnapshots(): void {
  selectedRepositoryId = null;
  repositorySnapshots = [];
  clearSnapshotSelection();
  if (overview) renderOverview(overview);
}


async function selectSnapshot(snapshotId: string): Promise<void> {
  selectedSnapshotId = snapshotId;
  if (overview) renderOverview(overview);
  setHtml("snapshot-detail", '<p class="text-xs text-white/35">Snapshot \uC0C1\uC138\uB97C \uC870\uD68C\uD558\uACE0 \uC788\uC2B5\uB2C8\uB2E4.</p>');
  setHtml("snapshot-tree-list", '<p class="py-8 text-center text-xs text-white/30">GitHub Tree\uB97C \uC870\uD68C\uD558\uACE0 \uC788\uC2B5\uB2C8\uB2E4.</p>');
  setHtml("snapshot-file-content", "");
  setText("snapshot-file-title", "\uD30C\uC77C\uC744 \uC120\uD0DD\uD558\uC138\uC694");
  setText("snapshot-file-meta", "Tree\uC5D0\uC11C UTF-8 \uD30C\uC77C\uC744 \uC120\uD0DD\uD558\uBA74 \uB0B4\uC6A9\uC744 \uD45C\uC2DC\uD569\uB2C8\uB2E4.");
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
  setText("snapshot-file-meta", "\uD30C\uC77C \uB0B4\uC6A9\uC744 \uC870\uD68C\uD558\uACE0 \uC788\uC2B5\uB2C8\uB2E4.");
  setHtml("snapshot-file-content", "");
  try {
    const value = await requestJson<FileResponse>(
      `/snapshots/${encodeURIComponent(selectedSnapshotId)}/file?path=${encodeURIComponent(path)}`,
    );
    renderFile(value);
  } catch (error) {
    showError("snapshot-file-content", error);
    setText("snapshot-file-meta", "\uC870\uD68C \uC2E4\uD328");
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
            <p class="mt-1 max-w-3xl text-xs leading-5 text-white/38">Public GitHub Commit Snapshot\uC758 \uC800\uC7A5 \uC0C1\uD0DC, Locator, Resolve, Tree\uC640 UTF-8 \uD30C\uC77C \uB0B4\uC6A9\uC744 \uAD00\uB9AC\uC790 \uB0B4\uBD80 \uACBD\uB85C\uC5D0\uC11C \uD655\uC778\uD569\uB2C8\uB2E4.</p>
          </div>
          <div class="flex items-center gap-2">
            <span id="snapshot-last-refresh" class="font-mono text-[9px] text-white/28">\uAC31\uC2E0 \uC900\uBE44 \uC911</span>
            <button id="snapshot-page-refresh" type="button" class="rounded-xl bg-mint-400 px-3.5 py-2 text-xs font-bold text-ink-950 hover:bg-mint-300 disabled:opacity-50">\uC0C8\uB85C\uACE0\uCE68</button>
          </div>
        </div>


        <div id="snapshot-status-banner"><p class="text-xs text-white/35">Snapshot \uC0C1\uD0DC\uB97C \uC870\uD68C\uD558\uACE0 \uC788\uC2B5\uB2C8\uB2E4.</p></div>



        <article class="panel rounded-2xl p-4">
          <div class="flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <h2 class="text-sm font-semibold text-white/85">GitHub Repository \uB4F1\uB85D \u00B7 Snapshot \uC0DD\uC131</h2>
              <p class="mt-1 text-[10px] leading-5 text-white/35">\uACF5\uAC1C github.com Repository URL\uACFC Branch, Tag \uB610\uB294 Commit SHA\uB97C \uC785\uB825\uD558\uBA74 Repository \uC815\uBCF4\uB97C \uC800\uC7A5\uD558\uACE0 \uAC80\uC99D\uB41C Commit Snapshot\uC744 \uB4F1\uB85D\uD569\uB2C8\uB2E4. Ref\uB97C \uBE44\uC6B0\uBA74 \uAE30\uBCF8 Branch\uB97C \uC0AC\uC6A9\uD569\uB2C8\uB2E4.</p>
            </div>
            <span class="rounded-lg border border-amber-300/15 bg-amber-300/5 px-2.5 py-1.5 text-[9px] text-amber-300">Public Repository Only</span>
          </div>
          <form id="snapshot-import-form" class="mt-4 grid gap-3 lg:grid-cols-[minmax(280px,1.6fr)_minmax(180px,0.7fr)_auto] lg:items-end" autocomplete="off">
            <label class="block min-w-0">
              <span class="mb-1 block text-[9px] text-white/35">GitHub Repository URL</span>
              <input id="snapshot-import-url" class="playground-control w-full" type="url" required placeholder="https://github.com/owner/repository" />
            </label>
            <label class="block min-w-0">
              <span class="mb-1 block text-[9px] text-white/35">Ref \u00B7 \uC120\uD0DD</span>
              <input id="snapshot-import-ref" class="playground-control w-full" type="text" maxlength="255" placeholder="main, tag, commit SHA" />
            </label>
            <button id="snapshot-import-submit" type="submit" class="rounded-xl bg-mint-400 px-4 py-2.5 text-xs font-bold text-ink-950 hover:bg-mint-300 disabled:cursor-wait disabled:opacity-50">Repository &amp; Snapshot \uAC00\uC838\uC624\uAE30</button>
          </form>
          <div id="snapshot-import-result" class="mt-3 min-h-5 text-[10px] text-white/38" aria-live="polite">\uAD00\uB9AC\uC790 \uB0B4\uBD80 Proxy\uB97C \uD1B5\uD574\uB9CC \uB4F1\uB85D\uD558\uBA70 Snapshot Token\uC740 \uBE0C\uB77C\uC6B0\uC800\uC5D0 \uC804\uB2EC\uD558\uC9C0 \uC54A\uC2B5\uB2C8\uB2E4.</div>
        </article>


        <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          ${["repositories", "snapshots", "locators", "tables"].map((key) => {
            const labels: Record<string, string> = { repositories: "Repositories", snapshots: "Snapshots", locators: "Locators", tables: "DB Tables" };
            return `<article class="panel rounded-2xl p-4"><p class="text-[10px] uppercase tracking-widest text-white/30">${labels[key]}</p><p id="snapshot-count-${key}" class="mt-2 font-mono text-2xl font-semibold text-white/85">-</p></article>`;
          }).join("")}
        </div>


        <article class="panel rounded-2xl p-4">
          <div class="grid gap-2 text-[10px] text-white/45 md:grid-cols-3">
            <p>Tenant \u00B7 <span id="snapshot-runtime-tenant" class="font-mono text-white/65">-</span></p>
            <p>\uC6B4\uC601 Token \u00B7 <span id="snapshot-runtime-token" class="font-mono text-white/65">-</span></p>
            <p class="truncate">Allowlist \u00B7 <span id="snapshot-runtime-allowlist" class="font-mono text-white/65">-</span></p>
          </div>
        </article>


        <div class="grid gap-4 xl:grid-cols-[minmax(260px,0.8fr)_minmax(320px,1fr)]">
          <article class="panel rounded-2xl p-4">
            <div class="flex items-center justify-between gap-3"><h2 class="text-sm font-semibold text-white/85">Repositories</h2><button id="snapshot-show-all" type="button" class="rounded-lg border border-white/10 px-2.5 py-1.5 text-[9px] text-white/50 hover:border-white/20 hover:text-white/75">전체 Snapshot</button></div>
            <div id="snapshot-repository-list" class="mt-3 max-h-[480px] space-y-2 overflow-y-auto"><p class="text-xs text-white/30">\uC870\uD68C \uC911</p></div>
          </article>
          <article class="panel rounded-2xl p-4">
            <div class="flex items-center justify-between gap-3"><h2 id="snapshot-list-title" class="text-sm font-semibold text-white/85">전체 Commit Snapshots</h2><span id="snapshot-page-label" class="font-mono text-[9px] text-white/30">\uC870\uD68C \uC911</span></div>
            <div id="snapshot-record-list" class="mt-3 max-h-[480px] space-y-2 overflow-y-auto"><p class="text-xs text-white/30">\uC870\uD68C \uC911</p></div>
            <div class="mt-3 flex justify-end gap-2"><button id="snapshot-page-previous" type="button" class="rounded-lg border border-white/10 px-3 py-2 text-[10px] text-white/55 disabled:opacity-30">\uC774\uC804</button><button id="snapshot-page-next" type="button" class="rounded-lg border border-white/10 px-3 py-2 text-[10px] text-white/55 disabled:opacity-30">\uB2E4\uC74C</button></div>
          </article>
        </div>


        <article class="panel rounded-2xl p-4">
          <h2 class="text-sm font-semibold text-white/85">Snapshot Identity \u00B7 Locator \u00B7 AccessPlan</h2>
          <div id="snapshot-detail" class="mt-3"><p class="text-xs text-white/30">Snapshot\uC744 \uC120\uD0DD\uD558\uC138\uC694.</p></div>
        </article>


        <div class="grid gap-4 xl:grid-cols-[minmax(360px,0.9fr)_minmax(0,1.4fr)]">
          <article class="panel min-h-[520px] rounded-2xl p-4">
            <div class="flex items-end justify-between gap-3"><label class="block min-w-0 flex-1"><span class="mb-1 block text-[9px] text-white/35">Tree \uD544\uD130</span><input id="snapshot-tree-filter" class="playground-control w-full" type="search" placeholder="README, src/, compose..." /></label><span id="snapshot-tree-count" class="pb-2 font-mono text-[9px] text-white/30">0/0</span></div>
            <div id="snapshot-tree-list" class="mt-3 max-h-[430px] overflow-y-auto rounded-xl border border-white/7"><p class="py-8 text-center text-xs text-white/30">Snapshot\uC744 \uC120\uD0DD\uD558\uC138\uC694.</p></div>
          </article>
          <article class="panel min-h-[520px] min-w-0 rounded-2xl p-4">
            <div class="flex flex-col gap-1 border-b border-white/7 pb-3"><h2 id="snapshot-file-title" class="truncate text-sm font-semibold text-white/85">\uD30C\uC77C\uC744 \uC120\uD0DD\uD558\uC138\uC694</h2><p id="snapshot-file-meta" class="font-mono text-[9px] text-white/30">Tree\uC5D0\uC11C UTF-8 \uD30C\uC77C\uC744 \uC120\uD0DD\uD558\uBA74 \uB0B4\uC6A9\uC744 \uD45C\uC2DC\uD569\uB2C8\uB2E4.</p></div>
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
  document.getElementById("snapshot-import-form")?.addEventListener("submit", (event) => void importRepositorySnapshot(event as SubmitEvent));
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
  document.getElementById("snapshot-repository-list")?.addEventListener("click", (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-repository-id]");
    const repositoryId = button?.dataset.repositoryId;
    if (repositoryId) void selectRepository(repositoryId);
  });
  document.getElementById("snapshot-show-all")?.addEventListener("click", showAllSnapshots);
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
