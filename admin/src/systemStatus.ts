export const SYSTEM_STATUS_ROUTE = "/system-status";

type SystemStatusIcons = {
  cloud: string;
  pulse: string;
  cube: string;
  database: string;
  arrow: string;
};

type APIEndpointActivity = {
  method: string;
  path: string;
  requested: boolean;
  responded: boolean;
  success: boolean;
  last_status_code: number | null;
  last_request_at: string | null;
  last_response_at: string | null;
  last_success_at: string | null;
  last_duration_ms: number | null;
  last_request_id: string | null;
  client_id: string | null;
  request_count: number;
  success_count: number;
  error_count: number;
};

type APIEndpointActivityResponse = {
  checked_at: string;
  endpoints: APIEndpointActivity[];
};

type FrontendConnectivity = {
  status: "online" | "stale" | "offline" | "unknown";
  connected: boolean;
  client_id: string | null;
  project_id: string | null;
  client_version: string | null;
  last_event: string | null;
  last_seen_at: string | null;
  age_seconds: number | null;
};

type AIConnectivity = {
  status: "online" | "degraded" | "offline";
  connected: boolean;
  model_id: string;
  model: string;
  model_available: boolean;
  latency_ms: number;
  error: string | null;
};

type ConnectivityResponse = {
  checked_at: string;
  frontend: FrontendConnectivity;
  backendai: AIConnectivity;
};

type CommunicationEvent = {
  event_id: number;
  occurred_at: string;
  request_id: string;
  channel: string;
  direction: string;
  phase: string;
  status: string;
  method: string | null;
  path: string | null;
  client_id: string | null;
  project_id: string | null;
  status_code: number | null;
  duration_ms: number | null;
  provider: string | null;
  model: string | null;
  source_count: number | null;
  error: string | null;
  details: Record<string, unknown>;
};

type CommunicationEventListResponse = {
  checked_at: string;
  retention_days: number;
  events: CommunicationEvent[];
};

type ChatAuditLog = {
  request_id: string;
  received_at: string;
  completed_at: string | null;
  client_id: string | null;
  project_id: string;
  session_id: string;
  requested_model_id: string | null;
  message: string | null;
  message_truncated: boolean;
  history_count: number;
  context_chars: number;
  status: string;
  status_code: number | null;
  answer: string | null;
  answer_truncated: boolean;
  used_model_id: string | null;
  provider: string | null;
  source_count: number | null;
  duration_ms: number | null;
  error: string | null;
};

type ChatAuditLogListResponse = {
  checked_at: string;
  retention_days: number;
  content_limit_chars: number;
  logs: ChatAuditLog[];
};

type FrontendRegistrationEvent = {
  event_id: number;
  occurred_at: string;
  request_id: string;
  event_type: string;
  status: string;
  client_id: string | null;
  instance_id: string | null;
  client_name: string | null;
  declared_user: string | null;
  client_version: string | null;
  source_ip: string | null;
  registration_type: string | null;
  identification_method: string | null;
  is_first_connection: boolean;
  reason: string | null;
};

type FrontendRegistrationEventListResponse = {
  checked_at: string;
  retention_policy: "registry_lifetime";
  events: FrontendRegistrationEvent[];
};

type AIProbeResponse = {
  status: "ok" | "unexpected_answer";
  request_id: string;
  checked_at: string;
  requested_model_id: string;
  used_model_id: string;
  provider: string;
  model: string;
  latency_ms: number;
  answer_preview: string;
};

type ProviderDetails = {
  aiModel: string;
  embeddingModel: string;
  vectorProvider: string;
  backendVersion: string;
};

type BadgeTone = "ok" | "warning" | "error" | "idle" | "checking";

const endpointStatusIds: Record<string, string> = {
  "GET /v1/health": "health",
  "GET /v1/models": "models",
  "GET /v1/IngestResponse": "projects",
  "GET /v1/indexing-jobs": "indexing",
  "POST /v1/client-heartbeat": "heartbeat",
  "POST /v1/documents/ingest": "ingest",
  "POST /v1/projects/{project_id}/version/check": "version",
  "POST /v1/chat": "chat",
};

const channelLabels: Record<string, string> = {
  "frontend-fastapi": "Frontend ↔ FastAPI",
  "public-fastapi": "Public Client ↔ FastAPI",
  rag: "FastAPI ↔ VectorDB",
  "fastapi-ai": "FastAPI ↔ AI Server",
};

const phaseLabels: Record<string, string> = {
  "http.exchange": "API 요청·응답",
  "rag.request": "RAG 검색 요청",
  "rag.response": "RAG 검색 응답",
  "ai.request": "AI 추론 요청",
  "ai.response": "AI 추론 응답",
};

const escapeHtml = (value: unknown): string =>
  String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[character] ?? character);

const formatDateTime = (value: string | null): string => {
  if (!value) return "--";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? "--"
    : parsed.toLocaleString("ko-KR", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
};

const formatDuration = (value: number | null): string =>
  value === null ? "--" : `${value.toLocaleString("ko-KR")}ms`;

const setText = (id: string, value: string): void => {
  const element = document.getElementById(id);
  if (element) element.textContent = value;
};

let allCommunicationEvents: CommunicationEvent[] = [];
let allChatAuditLogs: ChatAuditLog[] = [];
let allFrontendRegistrationEvents: FrontendRegistrationEvent[] = [];
let communicationFilterQuery = "";
let chatAuditFilterQuery = "";
let frontendRegistrationFilterQuery = "";
type LogSortOrder = "newest" | "oldest";
let communicationLogSortOrder: LogSortOrder = "newest";
let chatAuditLogSortOrder: LogSortOrder = "newest";
let frontendRegistrationLogSortOrder: LogSortOrder = "newest";

const badgeClasses: Record<BadgeTone, string> = {
  ok: "border-mint-300/20 bg-mint-400/7 text-mint-300",
  warning: "border-amber-300/20 bg-amber-300/7 text-amber-300",
  error: "border-danger-300/20 bg-danger-300/7 text-danger-300",
  idle: "border-white/10 bg-white/4 text-white/42",
  checking: "border-sky-300/20 bg-sky-300/7 text-sky-200",
};

function setBadge(id: string, value: string, tone: BadgeTone): void {
  const element = document.getElementById(id);
  if (!element) return;
  element.textContent = value;
  element.className = `shrink-0 rounded-full border px-2.5 py-1 text-[10px] ${badgeClasses[tone]}`;
}

const sectionHeading = (title: string, subtitle: string, action = ""): string =>
  `<div class="flex items-start justify-between gap-3"><div><h2 class="text-sm font-semibold text-white/90">${title}</h2><p class="mt-0.5 text-[11px] text-white/35">${subtitle}</p></div>${action || '<span class="mt-1 size-1.5 rounded-full bg-mint-300/75"></span>'}</div>`;

function serviceRow(
  name: string,
  meta: string,
  status: string,
  tone: "ready" | "checking" | "idle",
  svg: string,
  id = "",
): string {
  const toneClass = tone === "ready"
    ? "text-mint-300 bg-mint-400/8"
    : tone === "checking"
      ? "text-amber-300 bg-amber-300/7"
      : "text-white/38 bg-white/4";
  return `<div class="flex items-center gap-2 rounded-xl border border-white/6 bg-black/10 p-2.5"><div class="grid size-8 shrink-0 place-items-center rounded-lg ${toneClass}">${svg}</div><div class="min-w-0"><p class="truncate text-xs font-medium text-white/78">${name}</p><p class="truncate font-mono text-[9px] text-white/30">${meta}</p></div><span id="${id}" class="ml-auto shrink-0 rounded-full border border-white/7 px-2 py-1 text-[9px] text-white/40">${status}</span></div>`;
}

function flowCard(
  title: string,
  path: string,
  statusId: string,
  detailId: string,
  requestId: string,
): string {
  return `<article class="rounded-xl border border-white/7 bg-black/12 p-3">
    <div class="flex items-center justify-between gap-3">
      <p class="text-xs font-semibold text-white/75">${title}</p>
      <span id="${statusId}" class="shrink-0 rounded-full border border-white/10 bg-white/4 px-2.5 py-1 text-[10px] text-white/42">확인 중</span>
    </div>
    <p class="mt-2 font-mono text-[9px] text-mint-300/55">${path}</p>
    <p id="${detailId}" class="mt-2 min-h-8 text-[10px] leading-4 text-white/38">상태 데이터를 불러오고 있습니다.</p>
    <p id="${requestId}" class="mt-2 truncate font-mono text-[9px] text-white/23">request_id --</p>
  </article>`;
}

const detailRow = (label: string, id: string): string =>
  `<div class="flex items-center justify-between gap-4 py-2.5 first:pt-0 last:pb-0"><dt class="text-[11px] text-white/38">${label}</dt><dd id="${id}" class="max-w-[64%] truncate text-right font-mono text-[10px] text-white/70">확인 중</dd></div>`;

function endpointRow(
  method: string,
  path: string,
  label: string,
  statusId: string,
): string {
  return `<div class="rounded-lg border border-white/6 bg-black/10 px-2.5 py-2">
    <div class="flex items-center gap-3">
      <span class="w-9 text-[10px] font-bold text-mint-300">${method}</span>
      <span class="min-w-0 flex-1 truncate text-white/62">${path}</span>
      <span class="text-[10px] text-white/25">${label}</span>
    </div>
    <div class="mt-1.5 flex flex-wrap items-center gap-1.5 pl-12">
      <span id="endpoint-${statusId}-request" class="rounded-md border border-white/8 bg-white/3 px-2 py-1 text-[9px] text-white/38">REQUEST FALSE</span>
      <span id="endpoint-${statusId}-response" class="rounded-md border border-white/8 bg-white/3 px-2 py-1 text-[9px] text-white/38">RESPONSE FALSE</span>
      <span id="endpoint-${statusId}-outcome" class="rounded-md border border-white/8 bg-white/3 px-2 py-1 text-[9px] text-white/38">NO RESULT</span>
      <span id="endpoint-${statusId}-detail" class="ml-auto text-[9px] text-white/25">호출 기록 없음</span>
    </div>
  </div>`;
}

const activityRow = (title: string, description: string, time: string): string =>
  `<div class="flex gap-3"><span class="mt-1.5 size-2 shrink-0 rounded-full bg-mint-300/80"></span><div class="min-w-0 flex-1 border-b border-white/6 pb-4 last:border-0 last:pb-0"><div class="flex items-start justify-between gap-4"><p class="text-sm text-white/72">${escapeHtml(title)}</p><time class="shrink-0 font-mono text-[10px] text-white/25">${escapeHtml(time)}</time></div><p class="mt-1 text-xs text-white/32">${escapeHtml(description)}</p></div></div>`;

export function systemStatusMarkup(
  apiBaseUrl: string,
  icons: SystemStatusIcons,
): string {
  const logRefreshAction = `<button id="system-log-refresh" type="button" class="rounded-lg border border-white/10 px-2.5 py-1.5 text-[10px] font-semibold text-white/55 transition hover:bg-white/5 disabled:opacity-45">로그 새로고침</button>`;
  return `
    <div class="mx-auto max-w-[1280px] px-4 py-5 sm:px-6 lg:px-8">
      <section class="enter space-y-3">
        <article class="panel rounded-2xl p-4">
          ${sectionHeading("통신 경로 진단", "실제 Frontend 요청, FastAPI 처리, RAG 검색과 AI 응답을 분리해 확인합니다.")}
          <div class="mt-3 grid gap-2 md:grid-cols-3">
            ${flowCard("Frontend → FastAPI", "VS Code Extension → /v1/*", "flow-frontend-status", "flow-frontend-detail", "flow-frontend-request-id")}
            ${flowCard("FastAPI → VectorDB", "BGE-M3 → Qdrant project scope", "flow-rag-status", "flow-rag-detail", "flow-rag-request-id")}
            ${flowCard("FastAPI → AI Server", "Model Router → /api/chat 또는 Cloud", "flow-ai-status", "flow-ai-detail", "flow-ai-request-id")}
          </div>
          <div class="mt-3 flex flex-wrap items-center gap-2">
            <button id="system-ai-probe" type="button" class="rounded-lg bg-mint-400 px-3 py-2 text-[10px] font-bold text-ink-950 transition hover:bg-mint-300 disabled:cursor-wait disabled:opacity-50">AI 실제 대화 Probe</button>
            <span id="system-ai-probe-result" class="text-[10px] text-white/35">수동 Probe는 선택된 기본 모델에 VISION_AI_OK 응답을 요청합니다.</span>
            <span id="system-log-updated" class="ml-auto font-mono text-[9px] text-white/23">로그 대기</span>
          </div>
        </article>

        <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          <article class="panel rounded-2xl p-4">
            ${sectionHeading("서비스 토폴로지", "요청 경로와 내부 구성 요소")}
            <div class="mt-3 space-y-2">
              ${serviceRow("Traefik", "LAN reverse proxy · :80/:8000", "상태 수집", "checking", icons.cloud)}
              ${serviceRow("Granian + FastAPI", "ASGI · port 8000", "확인 중", "checking", icons.pulse, "api-service-status")}
              ${serviceRow("AI Model", "Model routing · /v1/chat", "확인 중", "checking", icons.cube, "ai-service-status")}
              ${serviceRow("Vector Store", "Project scoped", "확인 중", "checking", icons.database, "vector-service-status")}
              ${serviceRow("PostgreSQL · Qdrant · Redis", "Docker internal network", "상태 수집", "idle", icons.database)}
            </div>
          </article>
          <article class="panel rounded-2xl p-4">
            ${sectionHeading("Provider 구성", "API가 공개한 안전한 설정 정보")}
            <dl class="mt-3 divide-y divide-white/7">
              ${detailRow("AI model", "ai-model")}
              ${detailRow("Embedding", "embedding-model")}
              ${detailRow("Vector DB", "vector-provider")}
              ${detailRow("Backend version", "backend-version")}
            </dl>
            <p class="mt-3 text-[9px] leading-4 text-white/25">일반 통신 로그에는 본문을 저장하지 않습니다. 별도 Chat 감사 로그에는 질문·답변만 제한적으로 보관하며 Context, History 본문과 API Key는 저장하지 않습니다.</p>
          </article>
          <article class="panel rounded-2xl p-4">
            ${sectionHeading("API Endpoint", "Frontend 팀 공개 계약")}
            <div class="mt-3 max-h-[410px] space-y-1.5 overflow-y-auto pr-1 font-mono text-xs">
              ${endpointRow("GET", "/v1/health", "상태", "health")}
              ${endpointRow("GET", "/v1/models", "정확한 모델명", "models")}
              ${endpointRow("GET", "/v1/IngestResponse", "프로젝트 목록", "projects")}
              ${endpointRow("GET", "/v1/indexing-jobs", "Vector화 상태", "indexing")}
              ${endpointRow("POST", "/v1/client-heartbeat", "Frontend 연결", "heartbeat")}
              ${endpointRow("POST", "/v1/documents/ingest", "인덱싱", "ingest")}
              ${endpointRow("POST", "/v1/projects/{project_id}/version/check", "버전 비교", "version")}
              ${endpointRow("POST", "/v1/chat", "RAG 채팅", "chat")}
            </div>
            <p class="mt-3 text-[9px] leading-4 text-white/28">RESPONSE TRUE는 오류 응답을 포함해 FastAPI가 응답을 반환했음을 뜻하며, SUCCESS/ERROR가 최종 처리 결과를 표시합니다.</p>
            <a href="${apiBaseUrl}/docs" target="_blank" rel="noreferrer" class="mt-3 flex items-center justify-between rounded-xl border border-mint-300/15 bg-mint-400/5 px-3 py-2 text-[11px] font-semibold text-mint-300 transition hover:bg-mint-400/10"><span>OpenAPI 명세 열기</span>${icons.arrow}</a>
          </article>
        </div>

        <article class="panel rounded-2xl p-4">
          ${sectionHeading("요청 · 응답 통신 로그", "request_id로 Frontend API, RAG, AI 추론 단계를 추적합니다.", logRefreshAction)}
          <div class="mt-3 flex flex-wrap items-center justify-between gap-2">
            <label class="flex min-w-[240px] items-center gap-2 text-[10px] text-white/35">
              <span>필터</span>
              <input id="communication-log-filter" type="search" placeholder="경로 · 상태 · Client · 모델 · 오류 검색" class="w-full min-w-[220px] rounded-lg border border-white/8 bg-black/15 px-2.5 py-1.5 text-[10px] text-white/70 outline-none" />
            </label>
            <div class="flex items-center gap-2">
              <label class="flex items-center gap-1.5 text-[10px] text-white/35"><span>정렬</span><select id="communication-log-sort" class="rounded-lg border border-white/8 bg-black/15 px-2 py-1.5 text-[10px] text-white/70 outline-none"><option value="newest">최신순</option><option value="oldest">오래된순</option></select></label>
              <span id="communication-log-filter-count" class="font-mono text-[9px] text-white/23">0건</span>
            </div>
          </div>
          <div class="mt-3 overflow-x-auto rounded-xl border border-white/6">
            <table class="w-full min-w-[920px] border-collapse text-left">
              <thead class="bg-black/15 text-[9px] uppercase tracking-wider text-white/28">
                <tr>
                  <th class="px-3 py-2.5 font-medium">시각 / 경로</th>
                  <th class="px-3 py-2.5 font-medium">Request</th>
                  <th class="px-3 py-2.5 font-medium">Response</th>
                  <th class="px-3 py-2.5 font-medium">Project · Client</th>
                  <th class="px-3 py-2.5 font-medium">Provider · Model</th>
                  <th class="px-3 py-2.5 font-medium">request_id</th>
                </tr>
              </thead>
              <tbody id="communication-log-list" class="divide-y divide-white/6">
                <tr><td colspan="6" class="px-3 py-8 text-center text-xs text-white/30">통신 로그를 불러오고 있습니다.</td></tr>
              </tbody>
            </table>
          </div>
          <p class="mt-2 text-[9px] text-white/23">최근 7일 로그를 PostgreSQL에 보관하며 화면에는 최근 60개를 표시합니다.</p>
        </article>

        <article class="panel rounded-2xl p-4">
          ${sectionHeading("Frontend 최초 연결 · ID 등록 로그", "첫 Chat에서 Client를 식별하고 서버 ID를 발급한 과정을 추적합니다.")}
          <div class="mt-3 flex flex-wrap items-center justify-between gap-2">
            <label class="flex min-w-[240px] items-center gap-2 text-[10px] text-white/35">
              <span>필터</span>
              <input id="frontend-registration-log-filter" type="search" placeholder="Client · 사용자 · IP · 등록 결과 검색" class="w-full min-w-[220px] rounded-lg border border-white/8 bg-black/15 px-2.5 py-1.5 text-[10px] text-white/70 outline-none" />
            </label>
            <div class="flex items-center gap-2">
              <label class="flex items-center gap-1.5 text-[10px] text-white/35"><span>정렬</span><select id="frontend-registration-log-sort" class="rounded-lg border border-white/8 bg-black/15 px-2 py-1.5 text-[10px] text-white/70 outline-none"><option value="newest">최신순</option><option value="oldest">오래된순</option></select></label>
              <span id="frontend-registration-log-filter-count" class="font-mono text-[9px] text-white/23">0건</span>
            </div>
          </div>
          <div class="mt-3 overflow-x-auto rounded-xl border border-white/6">
            <table class="w-full min-w-[1080px] border-collapse text-left">
              <thead class="bg-black/15 text-[9px] uppercase tracking-wider text-white/28">
                <tr>
                  <th class="px-3 py-2.5 font-medium">시각 · 단계</th>
                  <th class="px-3 py-2.5 font-medium">사용자 · Client</th>
                  <th class="px-3 py-2.5 font-medium">발급 ID · Instance</th>
                  <th class="px-3 py-2.5 font-medium">접속 정보</th>
                  <th class="px-3 py-2.5 font-medium">식별 결과</th>
                  <th class="px-3 py-2.5 font-medium">request_id</th>
                </tr>
              </thead>
              <tbody id="frontend-registration-log-list" class="divide-y divide-white/6">
                <tr><td colspan="6" class="px-3 py-8 text-center text-xs text-white/30">Frontend 등록 로그를 불러오고 있습니다.</td></tr>
              </tbody>
            </table>
          </div>
          <p class="mt-2 text-[9px] text-white/23">X-Client-User가 없으면 Client 이름, 설치별 Instance ID와 최초 접속 IP로 구분합니다. 등록 이력은 Client Registry 감사 기록으로 유지됩니다.</p>
        </article>

        <article class="panel rounded-2xl p-4">
          ${sectionHeading("Frontend Chat 감사 로그", "Frontend 질문과 AI 답변을 관리자 전용으로 확인합니다.")}
          <div class="mt-3 flex flex-wrap items-center justify-between gap-2">
            <label class="flex min-w-[240px] items-center gap-2 text-[10px] text-white/35">
              <span>필터</span>
              <input id="chat-audit-log-filter" type="search" placeholder="질문 · 답변 · Project · 모델 · 상태 검색" class="w-full min-w-[220px] rounded-lg border border-white/8 bg-black/15 px-2.5 py-1.5 text-[10px] text-white/70 outline-none" />
            </label>
            <div class="flex items-center gap-2">
              <label class="flex items-center gap-1.5 text-[10px] text-white/35"><span>정렬</span><select id="chat-audit-log-sort" class="rounded-lg border border-white/8 bg-black/15 px-2 py-1.5 text-[10px] text-white/70 outline-none"><option value="newest">최신순</option><option value="oldest">오래된순</option></select></label>
              <span id="chat-audit-log-filter-count" class="font-mono text-[9px] text-white/23">0건</span>
            </div>
          </div>
          <div class="mt-3 overflow-x-auto rounded-xl border border-white/6">
            <table class="w-full min-w-[1120px] border-collapse text-left">
              <thead class="bg-black/15 text-[9px] uppercase tracking-wider text-white/28">
                <tr>
                  <th class="px-3 py-2.5 font-medium">시각 · 상태</th>
                  <th class="px-3 py-2.5 font-medium">Frontend 질문</th>
                  <th class="px-3 py-2.5 font-medium">AI 답변 · 오류</th>
                  <th class="px-3 py-2.5 font-medium">Project · Session · Client</th>
                  <th class="px-3 py-2.5 font-medium">Model · RAG</th>
                  <th class="px-3 py-2.5 font-medium">request_id</th>
                </tr>
              </thead>
              <tbody id="chat-audit-log-list" class="divide-y divide-white/6">
                <tr><td colspan="6" class="px-3 py-8 text-center text-xs text-white/30">Chat 감사 로그를 불러오고 있습니다.</td></tr>
              </tbody>
            </table>
          </div>
          <p id="chat-audit-log-policy" class="mt-2 text-[9px] text-white/23">질문·답변은 최근 7일, 항목별 최대 20,000자로 보관합니다. Context와 History는 크기·개수만 기록합니다.</p>
        </article>

        <article class="panel rounded-2xl p-4">
          ${sectionHeading("상태 이벤트", "이 브라우저에서 확인한 최근 기록")}
          <div class="mt-3 space-y-3" id="activity-list" aria-live="polite">${activityRow("시스템 상태 화면이 시작되었습니다", "통신 상태 확인을 준비합니다.", "방금")}</div>
        </article>
      </section>
    </div>`;
}

export function addSystemStatusEvent(title: string, description: string): void {
  const list = document.getElementById("activity-list");
  if (!list) return;
  const time = new Date().toLocaleTimeString("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
  });
  list.insertAdjacentHTML("afterbegin", activityRow(title, description, time));
  while (list.children.length > 5) list.lastElementChild?.remove();
}

export function setTopologyStatus(
  id: string,
  message: string,
  ok: boolean,
): void {
  const element = document.getElementById(id);
  if (!element) return;
  element.textContent = message;
  element.className = `ml-auto shrink-0 rounded-full border px-2.5 py-1 text-[10px] ${
    ok
      ? "border-mint-300/15 bg-mint-400/5 text-mint-300"
      : "border-danger-300/15 bg-danger-300/5 text-danger-300"
  }`;
}

export function setProviderDetails(details: ProviderDetails): void {
  setText("ai-model", details.aiModel);
  setText("embedding-model", details.embeddingModel);
  setText("vector-provider", details.vectorProvider);
  setText("backend-version", details.backendVersion);
}

function setEndpointBoolean(
  elementId: string,
  label: "REQUEST" | "RESPONSE",
  value: boolean,
): void {
  const element = document.getElementById(elementId);
  if (!element) return;
  element.textContent = `${label} ${value ? "TRUE" : "FALSE"}`;
  element.className = `rounded-md border px-2 py-1 text-[9px] ${
    value
      ? "border-mint-300/20 bg-mint-400/7 text-mint-300"
      : "border-white/8 bg-white/3 text-white/38"
  }`;
}

function setEndpointOutcome(statusId: string, endpoint: APIEndpointActivity): void {
  const element = document.getElementById(`endpoint-${statusId}-outcome`);
  if (!element) return;
  const label = !endpoint.responded
    ? "NO RESULT"
    : endpoint.success
      ? "SUCCESS"
      : "ERROR";
  const tone: BadgeTone = !endpoint.responded
    ? "idle"
    : endpoint.success
      ? "ok"
      : "error";
  element.textContent = label;
  element.className = `rounded-md border px-2 py-1 text-[9px] ${badgeClasses[tone]}`;
}

export async function loadSystemEndpointActivity(
  adminApiBaseUrl: string,
): Promise<void> {
  try {
    const response = await fetch(`${adminApiBaseUrl}/api-activity`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const activity = (await response.json()) as APIEndpointActivityResponse;
    for (const endpoint of activity.endpoints) {
      const statusId = endpointStatusIds[`${endpoint.method} ${endpoint.path}`];
      if (!statusId) continue;
      setEndpointBoolean(`endpoint-${statusId}-request`, "REQUEST", endpoint.requested);
      setEndpointBoolean(`endpoint-${statusId}-response`, "RESPONSE", endpoint.responded);
      setEndpointOutcome(statusId, endpoint);
      const detail = endpoint.last_response_at
        ? `${endpoint.last_status_code ?? "--"} · ${formatDuration(endpoint.last_duration_ms)} · 성공 ${endpoint.success_count}/전체 ${endpoint.request_count} · 오류 ${endpoint.error_count} · ${endpoint.client_id || "client 미분류"}`
        : "호출 기록 없음";
      setText(`endpoint-${statusId}-detail`, detail);
    }
    setText("system-log-updated", `Endpoint ${formatDateTime(activity.checked_at)}`);
  } catch (error) {
    const message = error instanceof Error ? error.message : "알 수 없는 오류";
    for (const statusId of Object.values(endpointStatusIds)) {
      setText(`endpoint-${statusId}-detail`, `상태 조회 실패 · ${message}`);
    }
    addSystemStatusEvent("Endpoint 상태 조회 실패", message);
  }
}

function logStatusTone(status: string): BadgeTone {
  if (status === "success") return "ok";
  if (status === "warning" || status === "started") return "warning";
  if (status === "error" || status === "failed") return "error";
  return "idle";
}

function communicationLogRow(event: CommunicationEvent): string {
  const isRequest = event.phase.endsWith(".request") || event.phase === "http.exchange";
  const isResponse = event.phase.endsWith(".response") || event.phase === "http.exchange";
  const responseText = event.status_code !== null
    ? `HTTP ${event.status_code}`
    : event.status === "started"
      ? "대기 중"
      : event.status.toUpperCase();
  const responseTone = logStatusTone(event.status);
  const path = event.path
    ? `${event.method || ""} ${event.path}`.trim()
    : phaseLabels[event.phase] || event.phase;
  const client = [event.project_id, event.client_id].filter(Boolean).join(" · ") || "--";
  const provider = [event.provider, event.model].filter(Boolean).join(" · ") || "--";
  const metrics = [
    event.duration_ms !== null ? formatDuration(event.duration_ms) : null,
    event.source_count !== null ? `sources ${event.source_count}` : null,
  ].filter(Boolean).join(" · ");
  const error = event.error ? `<p class="mt-1 max-w-[260px] truncate text-[9px] text-danger-300">${escapeHtml(event.error)}</p>` : "";
  return `<tr class="bg-black/5 align-top transition hover:bg-white/[0.025]">
    <td class="px-3 py-2.5">
      <p class="text-[10px] text-white/62">${escapeHtml(channelLabels[event.channel] || event.channel)}</p>
      <p class="mt-1 font-mono text-[9px] text-white/28">${escapeHtml(formatDateTime(event.occurred_at))} · ${escapeHtml(path)}</p>
    </td>
    <td class="px-3 py-2.5"><span class="rounded-md border px-2 py-1 text-[9px] ${isRequest ? badgeClasses.ok : badgeClasses.idle}">${isRequest ? "REQUEST" : "--"}</span></td>
    <td class="px-3 py-2.5">
      <span class="rounded-md border px-2 py-1 text-[9px] ${isResponse ? badgeClasses[responseTone] : badgeClasses.idle}">${escapeHtml(isResponse ? responseText : "PENDING")}</span>
      ${metrics ? `<p class="mt-1 text-[9px] text-white/28">${escapeHtml(metrics)}</p>` : ""}
      ${error}
    </td>
    <td class="max-w-[220px] px-3 py-2.5"><p class="truncate text-[10px] text-white/48" title="${escapeHtml(client)}">${escapeHtml(client)}</p></td>
    <td class="max-w-[220px] px-3 py-2.5"><p class="truncate text-[10px] text-white/48" title="${escapeHtml(provider)}">${escapeHtml(provider)}</p></td>
    <td class="max-w-[220px] px-3 py-2.5"><p class="truncate font-mono text-[9px] text-white/30" title="${escapeHtml(event.request_id)}">${escapeHtml(event.request_id)}</p></td>
  </tr>`;
}

function normalizeFilterQuery(value: string): string {
  return value.trim().toLowerCase();
}

function matchesFilter(values: unknown[], query: string): boolean {
  const terms = normalizeFilterQuery(query).split(/\s+/).filter(Boolean);
  if (terms.length === 0) return true;
  const haystack = values
    .filter((value) => value !== null && value !== undefined)
    .map((value) => typeof value === "string" ? value : String(value))
    .join(" ")
    .toLowerCase();
  return terms.every((term) => haystack.includes(term));
}

function detailsSearchText(details: Record<string, unknown>): string {
  try {
    return JSON.stringify(details);
  } catch {
    return "";
  }
}

function sortByOccurredAt<T>(
  values: T[],
  getTimestamp: (value: T) => string | null,
  order: LogSortOrder,
): T[] {
  const factor = order === "newest" ? -1 : 1;
  return [...values].sort((left, right) => {
    const leftTime = Date.parse(getTimestamp(left) || "");
    const rightTime = Date.parse(getTimestamp(right) || "");
    const normalizedLeft = Number.isNaN(leftTime) ? 0 : leftTime;
    const normalizedRight = Number.isNaN(rightTime) ? 0 : rightTime;
    return (normalizedLeft - normalizedRight) * factor;
  });
}

function filterCommunicationEvents(events: CommunicationEvent[], query: string): CommunicationEvent[] {
  return events.filter((event) => {
    return matchesFilter([
      channelLabels[event.channel] || event.channel,
      phaseLabels[event.phase] || event.phase,
      event.status,
      event.method,
      event.path,
      event.project_id,
      event.client_id,
      event.provider,
      event.model,
      event.error,
      event.request_id,
      event.status_code,
      event.duration_ms,
      event.source_count,
      event.direction,
      detailsSearchText(event.details),
    ], query);
  });
}

function filterChatAuditLogs(logs: ChatAuditLog[], query: string): ChatAuditLog[] {
  return logs.filter((log) => {
    return matchesFilter([
      log.status,
      log.status_code,
      log.project_id,
      log.session_id,
      log.client_id,
      log.message,
      log.answer,
      log.error,
      log.provider,
      log.requested_model_id,
      log.used_model_id,
      log.source_count,
      log.duration_ms,
      log.context_chars,
      log.history_count,
      log.request_id,
    ], query);
  });
}

function filterFrontendRegistrationEvents(events: FrontendRegistrationEvent[], query: string): FrontendRegistrationEvent[] {
  return events.filter((event) => {
    return matchesFilter([
      registrationEventLabels[event.event_type] || event.event_type,
      event.status,
      event.client_name,
      event.declared_user,
      event.client_id,
      event.instance_id,
      event.source_ip,
      event.client_version,
      event.reason,
      event.registration_type,
      event.identification_method,
      event.is_first_connection ? "first first_connection 최초 연결 신규 id" : "existing 기존 등록",
      event.request_id,
    ], query);
  });
}

function renderCommunicationLogs(events: CommunicationEvent[]): void {
  const list = document.getElementById("communication-log-list");
  if (!list) return;
  const filtered = filterCommunicationEvents(events, communicationFilterQuery);
  const sorted = sortByOccurredAt(filtered, (event) => event.occurred_at, communicationLogSortOrder);
  list.innerHTML = sorted.length > 0
    ? sorted.map(communicationLogRow).join("")
    : '<tr><td colspan="6" class="px-3 py-8 text-center text-xs text-white/30">조건에 맞는 로그가 없습니다.</td></tr>';
  setText("communication-log-filter-count", `${filtered.length}/${events.length}건`);
}

function chatAuditContent(
  value: string | null,
  truncated: boolean,
  emptyLabel: string,
): string {
  if (!value) return `<p class="text-[10px] text-white/25">${escapeHtml(emptyLabel)}</p>`;
  const preview = value.length > 110 ? `${value.slice(0, 110)}…` : value;
  const suffix = truncated ? " · 저장 한도에서 잘림" : "";
  return `<details class="group max-w-[360px]">
    <summary class="cursor-pointer list-none text-[10px] leading-4 text-white/62" title="전체 내용 펼치기">${escapeHtml(preview)}</summary>
    <pre class="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-white/6 bg-black/20 p-2 text-[9px] leading-4 text-white/48">${escapeHtml(value)}</pre>
    ${suffix ? `<p class="mt-1 text-[9px] text-amber-300/70">${escapeHtml(suffix)}</p>` : ""}
  </details>`;
}

function chatAuditLogRow(log: ChatAuditLog): string {
  const tone = log.status === "completed"
    ? "ok"
    : log.status === "received"
      ? "warning"
      : "error";
  const statusText = log.status_code === null
    ? log.status.toUpperCase()
    : `${log.status.toUpperCase()} · HTTP ${log.status_code}`;
  const answerOrError = log.answer
    ? chatAuditContent(log.answer, log.answer_truncated, "답변 없음")
    : log.error
      ? `<p class="max-w-[360px] whitespace-pre-wrap break-words text-[10px] leading-4 text-danger-300">${escapeHtml(log.error)}</p>`
      : chatAuditContent(null, false, log.status === "received" ? "응답 대기 중" : "답변 없음");
  const identity = [
    log.project_id,
    log.session_id,
    log.client_id,
  ].filter(Boolean);
  const model = [
    log.provider,
    log.used_model_id || log.requested_model_id,
  ].filter(Boolean).join(" · ") || "--";
  const rag = [
    log.source_count !== null ? `sources ${log.source_count}` : null,
    log.duration_ms !== null ? formatDuration(log.duration_ms) : null,
    `context ${log.context_chars.toLocaleString("ko-KR")}자`,
    `history ${log.history_count}개`,
  ].filter(Boolean).join(" · ");
  return `<tr class="bg-black/5 align-top transition hover:bg-white/[0.025]">
    <td class="px-3 py-2.5">
      <span class="rounded-md border px-2 py-1 text-[9px] ${badgeClasses[tone]}">${escapeHtml(statusText)}</span>
      <p class="mt-2 font-mono text-[9px] text-white/28">${escapeHtml(formatDateTime(log.received_at))}</p>
    </td>
    <td class="px-3 py-2.5">${chatAuditContent(log.message, log.message_truncated, "질문 없음")}</td>
    <td class="px-3 py-2.5">${answerOrError}</td>
    <td class="max-w-[240px] px-3 py-2.5">
      ${identity.map((value) => `<p class="truncate text-[9px] text-white/42" title="${escapeHtml(value)}">${escapeHtml(value)}</p>`).join("")}
    </td>
    <td class="max-w-[260px] px-3 py-2.5">
      <p class="truncate text-[10px] text-white/52" title="${escapeHtml(model)}">${escapeHtml(model)}</p>
      <p class="mt-1 text-[9px] leading-4 text-white/28">${escapeHtml(rag)}</p>
    </td>
    <td class="max-w-[220px] px-3 py-2.5"><p class="truncate font-mono text-[9px] text-white/30" title="${escapeHtml(log.request_id)}">${escapeHtml(log.request_id)}</p></td>
  </tr>`;
}

function renderChatAuditLogs(logs: ChatAuditLog[], error?: string): void {
  const list = document.getElementById("chat-audit-log-list");
  if (!list) return;
  if (error) {
    list.innerHTML = `<tr><td colspan="6" class="px-3 py-8 text-center text-xs text-danger-300">Chat 감사 로그 조회 실패 · ${escapeHtml(error)}</td></tr>`;
    setText("chat-audit-log-filter-count", "0건");
    return;
  }
  const filtered = filterChatAuditLogs(logs, chatAuditFilterQuery);
  const sorted = sortByOccurredAt(filtered, (log) => log.received_at, chatAuditLogSortOrder);
  list.innerHTML = sorted.length > 0
    ? sorted.map(chatAuditLogRow).join("")
    : '<tr><td colspan="6" class="px-3 py-8 text-center text-xs text-white/30">조건에 맞는 Chat 요청이 없습니다.</td></tr>';
  setText("chat-audit-log-filter-count", `${filtered.length}/${logs.length}건`);
}

const registrationEventLabels: Record<string, string> = {
  registration_attempt: "등록 시도",
  client_id_issued: "ID 발급 완료",
  client_recognized: "기존 Client 확인",
  registration_denied: "등록·접속 거부",
  registration_failed: "등록 처리 실패",
};

function frontendRegistrationLogRow(event: FrontendRegistrationEvent): string {
  const tone: BadgeTone = event.status === "success"
    ? "ok"
    : event.status === "started"
      ? "checking"
      : "error";
  const identity = event.declared_user || "사용자명 미제공";
  const clientName = event.client_name || "Client 이름 미제공";
  const result = event.is_first_connection
    ? "최초 연결 · 신규 ID"
    : event.event_type === "client_recognized"
      ? "기존 등록 확인"
      : event.reason || "--";
  return `<tr class="bg-black/5 align-top transition hover:bg-white/[0.025]">
    <td class="px-3 py-2.5">
      <span class="rounded-md border px-2 py-1 text-[9px] ${badgeClasses[tone]}">${escapeHtml(registrationEventLabels[event.event_type] || event.event_type)}</span>
      <p class="mt-2 font-mono text-[9px] text-white/28">${escapeHtml(formatDateTime(event.occurred_at))}</p>
    </td>
    <td class="max-w-[220px] px-3 py-2.5">
      <p class="truncate text-[10px] ${event.declared_user ? "text-mint-300/80" : "text-white/30"}" title="${escapeHtml(identity)}">${escapeHtml(identity)}</p>
      <p class="mt-1 truncate text-[9px] text-white/42" title="${escapeHtml(clientName)}">${escapeHtml(clientName)}</p>
    </td>
    <td class="max-w-[250px] px-3 py-2.5">
      <p class="truncate font-mono text-[9px] text-white/55" title="${escapeHtml(event.client_id || "--")}">${escapeHtml(event.client_id || "ID 발급 전")}</p>
      <p class="mt-1 truncate font-mono text-[9px] text-white/25" title="${escapeHtml(event.instance_id || "--")}">${escapeHtml(event.instance_id || "Instance ID 없음")}</p>
    </td>
    <td class="px-3 py-2.5">
      <p class="font-mono text-[10px] text-white/52">${escapeHtml(event.source_ip || "--")}</p>
      <p class="mt-1 text-[9px] text-white/28">${escapeHtml(event.client_version || "버전 미제공")}</p>
    </td>
    <td class="max-w-[240px] px-3 py-2.5">
      <p class="text-[10px] ${event.is_first_connection ? "text-mint-300" : "text-white/48"}">${escapeHtml(result)}</p>
      <p class="mt-1 text-[9px] text-white/28">${escapeHtml([event.registration_type, event.identification_method].filter(Boolean).join(" · ") || "--")}</p>
    </td>
    <td class="max-w-[220px] px-3 py-2.5"><p class="truncate font-mono text-[9px] text-white/30" title="${escapeHtml(event.request_id)}">${escapeHtml(event.request_id)}</p></td>
  </tr>`;
}

function renderFrontendRegistrationLogs(
  events: FrontendRegistrationEvent[],
  error?: string,
): void {
  const list = document.getElementById("frontend-registration-log-list");
  if (!list) return;
  if (error) {
    list.innerHTML = `<tr><td colspan="6" class="px-3 py-8 text-center text-xs text-danger-300">Frontend 등록 로그 조회 실패 · ${escapeHtml(error)}</td></tr>`;
    setText("frontend-registration-log-filter-count", "0건");
    return;
  }
  const filtered = filterFrontendRegistrationEvents(events, frontendRegistrationFilterQuery);
  const sorted = sortByOccurredAt(filtered, (event) => event.occurred_at, frontendRegistrationLogSortOrder);
  list.innerHTML = sorted.length > 0
    ? sorted.map(frontendRegistrationLogRow).join("")
    : '<tr><td colspan="6" class="px-3 py-8 text-center text-xs text-white/30">조건에 맞는 최초 연결 로그가 없습니다.</td></tr>';
  setText("frontend-registration-log-filter-count", `${filtered.length}/${events.length}건`);
}

function latestEvent(
  events: CommunicationEvent[],
  channel: string,
  phase?: string,
): CommunicationEvent | undefined {
  return events.find((event) =>
    event.channel === channel && (!phase || event.phase === phase)
  );
}

function applyFlowStatuses(
  connectivity: ConnectivityResponse,
  events: CommunicationEvent[],
): void {
  const frontendEvent = latestEvent(events, "frontend-fastapi", "http.exchange");
  const frontend = connectivity.frontend;
  const frontendTone: BadgeTone = frontend.status === "online"
    ? "ok"
    : frontend.status === "stale"
      ? "warning"
      : frontend.status === "offline"
        ? "error"
        : frontendEvent
          ? logStatusTone(frontendEvent.status)
          : "idle";
  setBadge(
    "flow-frontend-status",
    frontend.status === "online"
      ? "통신 중"
      : frontend.status === "stale"
        ? "Heartbeat 지연"
        : frontend.status === "offline"
          ? "연결 끊김"
          : frontendEvent
            ? "API 기록 있음"
            : "Heartbeat 대기",
    frontendTone,
  );
  setText(
    "flow-frontend-detail",
    frontend.last_seen_at
      ? `${frontend.client_id || "VS Code"} · ${frontend.project_id || "project 미지정"} · ${frontend.age_seconds ?? "--"}초 전`
      : frontendEvent
        ? `${frontendEvent.method || ""} ${frontendEvent.path || ""} → ${frontendEvent.status_code ?? frontendEvent.status} · ${formatDuration(frontendEvent.duration_ms)}`
        : "Frontend에서 X-Client-ID, X-Client-Type과 heartbeat 또는 API 요청을 보내야 합니다.",
  );
  setText(
    "flow-frontend-request-id",
    `request_id ${frontendEvent?.request_id || "--"}`,
  );

  const ragResponse = latestEvent(events, "rag", "rag.response");
  setBadge(
    "flow-rag-status",
    !ragResponse
      ? "Chat 기록 대기"
      : ragResponse.status === "success"
        ? `검색 성공 · ${ragResponse.source_count ?? 0}건`
        : "검색 실패",
    !ragResponse ? "idle" : logStatusTone(ragResponse.status),
  );
  setText(
    "flow-rag-detail",
    ragResponse
      ? `${ragResponse.project_id || "project 미지정"} · ${ragResponse.provider || "vector"} · ${formatDuration(ragResponse.duration_ms)}`
      : "RAG 채팅 요청 후 프로젝트 범위 검색 결과가 표시됩니다.",
  );
  setText("flow-rag-request-id", `request_id ${ragResponse?.request_id || "--"}`);

  const aiResponse = latestEvent(events, "fastapi-ai", "ai.response");
  const ai = connectivity.backendai;
  const aiTone: BadgeTone = aiResponse
    ? logStatusTone(aiResponse.status)
    : ai.status === "online"
      ? "ok"
      : ai.status === "degraded"
        ? "warning"
        : "error";
  setBadge(
    "flow-ai-status",
    aiResponse
      ? aiResponse.status === "success"
        ? "대화 응답 확인"
        : aiResponse.status === "warning"
          ? "대화 응답 경고"
          : "대화 응답 실패"
      : ai.status === "online"
        ? "BackendAI 모델 탐색 성공"
        : ai.status === "degraded"
          ? "BackendAI 모델 확인 필요"
          : "BackendAI 연결 실패",
    aiTone,
  );
  setText(
    "flow-ai-detail",
    aiResponse
      ? aiResponse.error
        ? `${aiResponse.provider || "provider"} · ${aiResponse.model || ai.model} · ${formatDuration(aiResponse.duration_ms)} · ${aiResponse.error}`
        : `${aiResponse.provider || "provider"} · ${aiResponse.model || ai.model} · ${formatDuration(aiResponse.duration_ms)}`
      : ai.model_available
        ? `${ai.model} · 모델 목록 응답 ${ai.latency_ms}ms · 실제 대화 Probe 대기`
        : `${ai.error || "AI Server 응답 없음"} · ${ai.model}`,
  );
  setText("flow-ai-request-id", `request_id ${aiResponse?.request_id || "--"}`);
}

export async function loadSystemCommunicationLogs(
  adminApiBaseUrl: string,
): Promise<void> {
  const [
    connectivityResult,
    logsResult,
    chatAuditResult,
    registrationResult,
  ] = await Promise.allSettled([
    fetch(`${adminApiBaseUrl}/connectivity`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    }),
    fetch(`${adminApiBaseUrl}/communication-logs?limit=60`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    }),
    fetch(`${adminApiBaseUrl}/chat-audit-logs?limit=40`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    }),
    fetch(`${adminApiBaseUrl}/frontend-registration-logs?limit=60`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    }),
  ]);
  try {
    if (connectivityResult.status === "rejected") throw connectivityResult.reason;
    if (logsResult.status === "rejected") throw logsResult.reason;
    if (!connectivityResult.value.ok) {
      throw new Error(`Connectivity HTTP ${connectivityResult.value.status}`);
    }
    if (!logsResult.value.ok) {
      throw new Error(`Communication log HTTP ${logsResult.value.status}`);
    }
    const connectivity = (await connectivityResult.value.json()) as ConnectivityResponse;
    const logs = (await logsResult.value.json()) as CommunicationEventListResponse;
    allCommunicationEvents = logs.events;
    renderCommunicationLogs(allCommunicationEvents);
    applyFlowStatuses(connectivity, allCommunicationEvents);

    if (chatAuditResult.status === "rejected") {
      const auditError = chatAuditResult.reason instanceof Error
        ? chatAuditResult.reason.message
        : "연결 실패";
      renderChatAuditLogs([], auditError);
    } else if (!chatAuditResult.value.ok) {
      renderChatAuditLogs([], `HTTP ${chatAuditResult.value.status}`);
    } else {
      const chatAudit = (await chatAuditResult.value.json()) as ChatAuditLogListResponse;
      allChatAuditLogs = chatAudit.logs;
      renderChatAuditLogs(allChatAuditLogs);
      setText(
        "chat-audit-log-policy",
        `질문·답변은 최근 ${chatAudit.retention_days}일, 항목별 최대 ${chatAudit.content_limit_chars.toLocaleString("ko-KR")}자로 보관합니다. Context와 History는 크기·개수만 기록합니다.`,
      );
    }

    if (registrationResult.status === "rejected") {
      const registrationError = registrationResult.reason instanceof Error
        ? registrationResult.reason.message
        : "연결 실패";
      renderFrontendRegistrationLogs([], registrationError);
    } else if (!registrationResult.value.ok) {
      renderFrontendRegistrationLogs([], `HTTP ${registrationResult.value.status}`);
    } else {
      const registrationLogs = (
        await registrationResult.value.json()
      ) as FrontendRegistrationEventListResponse;
      allFrontendRegistrationEvents = registrationLogs.events;
      renderFrontendRegistrationLogs(allFrontendRegistrationEvents);
    }

    setText(
      "system-log-updated",
      `통신 로그 ${formatDateTime(logs.checked_at)} · ${logs.events.length}건`,
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : "알 수 없는 오류";
    renderCommunicationLogs([]);
    renderChatAuditLogs([], message);
    renderFrontendRegistrationLogs([], message);
    setBadge("flow-frontend-status", "조회 실패", "error");
    setBadge("flow-rag-status", "조회 실패", "error");
    setBadge("flow-ai-status", "조회 실패", "error");
    setText("system-log-updated", `로그 조회 실패 · ${message}`);
    addSystemStatusEvent("통신 로그 조회 실패", message);
  }
}

async function runAIProbe(adminApiBaseUrl: string): Promise<void> {
  const button = document.getElementById("system-ai-probe") as HTMLButtonElement | null;
  if (!button || button.disabled) return;
  button.disabled = true;
  button.textContent = "AI 응답 대기 중…";
  setBadge("flow-ai-status", "실제 대화 확인 중", "checking");
  setText("system-ai-probe-result", "FastAPI가 기본 모델에 실제 비스트리밍 대화 요청을 전송했습니다.");
  try {
    const response = await fetch(`${adminApiBaseUrl}/ai-probe`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: "{}",
    });
    const body = await response.json().catch(() => null) as
      | AIProbeResponse
      | { detail?: string; request_id?: string }
      | null;
    if (!response.ok) {
      throw new Error(body && "detail" in body && body.detail
        ? body.detail
        : `HTTP ${response.status}`);
    }
    const probe = body as AIProbeResponse;
    const exact = probe.status === "ok";
    setBadge("flow-ai-status", exact ? "대화 Probe 성공" : "응답 형식 경고", exact ? "ok" : "warning");
    setText(
      "system-ai-probe-result",
      `${probe.provider} · ${probe.model} · ${formatDuration(probe.latency_ms)} · ${probe.answer_preview}`,
    );
    setText("flow-ai-request-id", `request_id ${probe.request_id}`);
    addSystemStatusEvent(
      exact ? "AI 실제 대화 Probe 성공" : "AI 응답 형식 경고",
      `${probe.model} · ${formatDuration(probe.latency_ms)} · ${probe.request_id}`,
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : "알 수 없는 오류";
    setBadge("flow-ai-status", "대화 Probe 실패", "error");
    setText("system-ai-probe-result", message);
    addSystemStatusEvent("AI 실제 대화 Probe 실패", message);
  } finally {
    button.disabled = false;
    button.textContent = "AI 실제 대화 Probe";
    await loadSystemCommunicationLogs(adminApiBaseUrl);
  }
}

export function initializeSystemStatus(adminApiBaseUrl: string): void {
  const probe = document.getElementById("system-ai-probe") as HTMLButtonElement | null;
  if (probe && probe.dataset.bound !== "true") {
    probe.dataset.bound = "true";
    probe.addEventListener("click", () => void runAIProbe(adminApiBaseUrl));
  }
  const refresh = document.getElementById("system-log-refresh") as HTMLButtonElement | null;
  if (refresh && refresh.dataset.bound !== "true") {
    refresh.dataset.bound = "true";
    refresh.addEventListener("click", () => {
      void Promise.allSettled([
        loadSystemEndpointActivity(adminApiBaseUrl),
        loadSystemCommunicationLogs(adminApiBaseUrl),
      ]);
    });
  }

  const communicationFilter = document.getElementById("communication-log-filter") as HTMLInputElement | null;
  if (communicationFilter && communicationFilter.dataset.bound !== "true") {
    communicationFilter.dataset.bound = "true";
    communicationFilter.addEventListener("input", () => {
      communicationFilterQuery = normalizeFilterQuery(communicationFilter.value);
      renderCommunicationLogs(allCommunicationEvents);
    });
  }
  const communicationSort = document.getElementById("communication-log-sort") as HTMLSelectElement | null;
  if (communicationSort && communicationSort.dataset.bound !== "true") {
    communicationSort.dataset.bound = "true";
    communicationSort.addEventListener("change", () => {
      communicationLogSortOrder = communicationSort.value === "oldest" ? "oldest" : "newest";
      renderCommunicationLogs(allCommunicationEvents);
    });
  }

  const registrationFilter = document.getElementById("frontend-registration-log-filter") as HTMLInputElement | null;
  if (registrationFilter && registrationFilter.dataset.bound !== "true") {
    registrationFilter.dataset.bound = "true";
    registrationFilter.addEventListener("input", () => {
      frontendRegistrationFilterQuery = normalizeFilterQuery(registrationFilter.value);
      renderFrontendRegistrationLogs(allFrontendRegistrationEvents);
    });
  }
  const registrationSort = document.getElementById("frontend-registration-log-sort") as HTMLSelectElement | null;
  if (registrationSort && registrationSort.dataset.bound !== "true") {
    registrationSort.dataset.bound = "true";
    registrationSort.addEventListener("change", () => {
      frontendRegistrationLogSortOrder = registrationSort.value === "oldest" ? "oldest" : "newest";
      renderFrontendRegistrationLogs(allFrontendRegistrationEvents);
    });
  }

  const chatFilter = document.getElementById("chat-audit-log-filter") as HTMLInputElement | null;
  if (chatFilter && chatFilter.dataset.bound !== "true") {
    chatFilter.dataset.bound = "true";
    chatFilter.addEventListener("input", () => {
      chatAuditFilterQuery = normalizeFilterQuery(chatFilter.value);
      renderChatAuditLogs(allChatAuditLogs);
    });
  }
  const chatSort = document.getElementById("chat-audit-log-sort") as HTMLSelectElement | null;
  if (chatSort && chatSort.dataset.bound !== "true") {
    chatSort.dataset.bound = "true";
    chatSort.addEventListener("change", () => {
      chatAuditLogSortOrder = chatSort.value === "oldest" ? "oldest" : "newest";
      renderChatAuditLogs(allChatAuditLogs);
    });
  }
}
