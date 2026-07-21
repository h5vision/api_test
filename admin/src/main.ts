import "./styles.css";

type ProviderStatus = {
  ai_provider: string;
  ai_model: string;
  ai_configured: boolean;
  embedding_provider: string;
  embedding_model: string;
  embedding_configured: boolean;
  vector_db_provider: string;
};

type HealthResponse = {
  status: string;
  service: string;
  version: string;
  configuration: ProviderStatus;
  vector_store: { projects: number; chunks: number };
};

const apiBaseUrl = (
  import.meta.env.VITE_API_BASE_URL || "https://api.blakeedenparker.cloud"
).replace(/\/$/, "");

const icon = (path: string, className = "size-5") => `
  <svg class="${className}" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path d="${path}" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" />
  </svg>`;

const icons = {
  grid: icon("M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z"),
  pulse: icon("M3 12h4l2.2-6 4.1 12 2.2-6H21"),
  cube: icon("m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Zm0 9 8-4.5M12 12 4 7.5M12 12v9"),
  database: icon("M5 6c0-1.7 3.1-3 7-3s7 1.3 7 3-3.1 3-7 3-7-1.3-7-3Zm0 0v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"),
  cloud: icon("M7 18h10a4 4 0 0 0 .5-8A6 6 0 0 0 6 8.7 4.5 4.5 0 0 0 7 18Z"),
  shield: icon("M12 3 5 6v5c0 4.6 2.8 8.1 7 10 4.2-1.9 7-5.4 7-10V6l-7-3Zm-3 9 2 2 4-4"),
  refresh: icon("M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6"),
  arrow: icon("M5 12h14m-5-5 5 5-5 5", "size-4"),
  external: icon("M14 4h6v6M20 4l-9 9M18 13v6H5V6h6", "size-4"),
};

function navItem(label: string, svg: string, active = false): string {
  const tone = active
    ? "bg-mint-400/10 text-mint-300"
    : "text-white/42 hover:bg-white/4 hover:text-white/75";
  const marker = active
    ? '<span class="ml-auto size-1.5 rounded-full bg-mint-300"></span>'
    : "";
  return `<button class="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm transition ${tone}">${svg}<span>${label}</span>${marker}</button>`;
}

function metricCard(label: string, unit: string, caption: string, id: string): string {
  return `<article class="panel rounded-2xl p-5"><p class="text-xs font-medium text-white/42">${label}</p><div class="mt-5 flex items-end gap-2"><strong id="${id}" class="max-w-full truncate text-3xl font-semibold tracking-[-0.04em] text-white">--</strong>${unit ? `<span class="mb-1 font-mono text-[10px] text-white/30">${unit}</span>` : ""}</div><p class="mt-2 text-[11px] text-white/28">${caption}</p></article>`;
}

function sectionHeading(title: string, subtitle: string): string {
  return `<div class="flex items-start justify-between gap-4"><div><h2 class="text-base font-semibold text-white/90">${title}</h2><p class="mt-1 text-xs text-white/35">${subtitle}</p></div><span class="mt-1 size-1.5 rounded-full bg-mint-300/75"></span></div>`;
}

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
  return `<div class="flex items-center gap-3 rounded-2xl border border-white/6 bg-black/10 p-3.5"><div class="grid size-10 shrink-0 place-items-center rounded-xl ${toneClass}">${svg}</div><div class="min-w-0"><p class="truncate text-sm font-medium text-white/78">${name}</p><p class="mt-0.5 truncate font-mono text-[10px] text-white/30">${meta}</p></div><span id="${id}" class="ml-auto shrink-0 rounded-full border border-white/7 px-2.5 py-1 text-[10px] text-white/40">${status}</span></div>`;
}

function detailRow(label: string, id: string): string {
  return `<div class="flex items-center justify-between gap-5 py-3.5 first:pt-0 last:pb-0"><dt class="text-xs text-white/38">${label}</dt><dd id="${id}" class="max-w-[64%] truncate text-right font-mono text-[11px] text-white/70">확인 중</dd></div>`;
}

function endpointRow(method: string, path: string, label: string): string {
  return `<div class="flex items-center gap-3 rounded-xl border border-white/6 bg-black/10 px-3 py-3"><span class="w-9 text-[10px] font-bold text-mint-300">${method}</span><span class="min-w-0 flex-1 truncate text-white/62">${path}</span><span class="text-[10px] text-white/25">${label}</span></div>`;
}

function activityRow(title: string, description: string, time: string): string {
  return `<div class="flex gap-3"><span class="mt-1.5 size-2 shrink-0 rounded-full bg-mint-300/80"></span><div class="min-w-0 flex-1 border-b border-white/6 pb-4 last:border-0 last:pb-0"><div class="flex items-start justify-between gap-4"><p class="text-sm text-white/72">${title}</p><time class="shrink-0 font-mono text-[10px] text-white/25">${time}</time></div><p class="mt-1 text-xs text-white/32">${description}</p></div></div>`;
}

document.querySelector<HTMLDivElement>("#app")!.innerHTML = `
  <div class="grid-surface min-h-screen lg:grid lg:grid-cols-[248px_1fr]">
    <aside class="hidden border-r border-white/7 bg-ink-950/75 px-5 py-7 backdrop-blur-xl lg:flex lg:flex-col">
      <div class="flex items-center gap-3 px-2">
        <div class="grid size-10 place-items-center rounded-xl border border-mint-300/25 bg-mint-400/10 text-mint-300"><span class="font-mono text-sm font-bold">V//</span></div>
        <div><p class="text-sm font-semibold tracking-[0.08em] text-white">VISION</p><p class="text-[10px] tracking-[0.24em] text-mint-300/70">CONTROL CENTER</p></div>
      </div>
      <nav class="mt-12 space-y-1" aria-label="관리자 메뉴">
        ${navItem("개요", icons.grid, true)}
        ${navItem("API 상태", icons.pulse)}
        ${navItem("AI 모델", icons.cube)}
        ${navItem("데이터 저장소", icons.database)}
        ${navItem("인프라", icons.cloud)}
      </nav>
      <div class="mt-auto rounded-2xl border border-amber-300/15 bg-amber-300/5 p-4">
        <div class="flex items-center gap-2 text-amber-300">${icons.shield}<span class="text-xs font-semibold">읽기 전용 콘솔</span></div>
        <p class="mt-2 text-xs leading-5 text-white/45">운영 변경 기능은 인증·감사 정책 연결 후 활성화됩니다.</p>
      </div>
    </aside>

    <main class="min-w-0">
      <header class="sticky top-0 z-20 border-b border-white/7 bg-ink-950/75 px-4 py-4 backdrop-blur-xl sm:px-7 lg:px-10">
        <div class="mx-auto flex max-w-[1440px] items-center justify-between gap-4">
          <div class="flex items-center gap-3 lg:hidden"><div class="grid size-9 place-items-center rounded-xl bg-mint-400/10 font-mono text-xs font-bold text-mint-300">V//</div><div><p class="text-sm font-semibold">Vision Control</p><p class="text-[10px] text-white/40">관리자 콘솔</p></div></div>
          <div class="hidden items-center gap-3 lg:flex"><span class="rounded-full border border-white/8 bg-white/4 px-3 py-1.5 font-mono text-[10px] tracking-widest text-white/50">PRODUCTION</span><span class="text-xs text-white/35" id="last-updated">마지막 동기화 준비 중</span></div>
          <div class="flex items-center gap-2">
            <a href="${apiBaseUrl}/docs" target="_blank" rel="noreferrer" class="hidden items-center gap-2 rounded-xl border border-white/10 px-3.5 py-2 text-xs font-medium text-white/65 transition hover:border-white/20 hover:bg-white/5 sm:flex">API 문서 ${icons.external}</a>
            <button id="refresh-button" class="flex items-center gap-2 rounded-xl bg-mint-400 px-3.5 py-2 text-xs font-bold text-ink-950 transition hover:bg-mint-300 disabled:cursor-wait disabled:opacity-60">${icons.refresh}<span class="hidden sm:inline">새로고침</span></button>
          </div>
        </div>
      </header>

      <div class="mx-auto max-w-[1440px] px-4 py-7 sm:px-7 lg:px-10 lg:py-10">
        <section class="enter flex flex-col gap-6 border-b border-white/7 pb-8 xl:flex-row xl:items-end xl:justify-between">
          <div><div class="mb-3 flex items-center gap-2 text-xs font-semibold tracking-[0.18em] text-mint-300/75"><span class="h-px w-8 bg-mint-300/50"></span> SYSTEM OVERVIEW</div><h1 class="text-3xl font-semibold tracking-[-0.04em] text-white sm:text-4xl">운영 현황을 한눈에.</h1><p class="mt-3 max-w-2xl text-sm leading-6 text-white/45">Cloudflare Edge부터 FastAPI와 Vector Store까지, 현재 배포 상태를 실시간으로 확인합니다.</p></div>
          <div id="global-status" class="flex w-fit items-center gap-3 rounded-2xl border border-white/8 bg-white/4 px-4 py-3"><span class="status-dot size-2 rounded-full bg-white/30"></span><div><p class="text-xs font-semibold text-white/75">상태 확인 중</p><p class="mt-0.5 font-mono text-[10px] text-white/35">${apiBaseUrl}</p></div></div>
        </section>

        <section class="enter enter-delay-1 mt-7 grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="핵심 지표">
          ${metricCard("API 응답시간", "ms", "실시간 상태 확인", "latency-value")}
          ${metricCard("인덱싱 청크", "chunks", "Vector Store 누적", "chunks-value")}
          ${metricCard("프로젝트", "projects", "분리된 검색 범위", "projects-value")}
          ${metricCard("AI Provider", "", "응답 생성 모델", "provider-value")}
        </section>

        <section class="enter enter-delay-2 mt-5 grid gap-5 xl:grid-cols-[1.2fr_0.8fr]">
          <article class="panel rounded-3xl p-5 sm:p-6">
            ${sectionHeading("서비스 토폴로지", "요청 경로와 내부 구성 요소")}
            <div class="mt-6 space-y-3">
              ${serviceRow("Cloudflare Tunnel", "Edge connector", "외부 연결", "ready", icons.cloud)}
              ${serviceRow("Granian + FastAPI", "ASGI · port 8000", "확인 중", "checking", icons.pulse, "api-service-status")}
              ${serviceRow("NVIDIA AI", "Chat completions", "확인 중", "checking", icons.cube, "ai-service-status")}
              ${serviceRow("Vector Store", "Project scoped", "확인 중", "checking", icons.database, "vector-service-status")}
              ${serviceRow("PostgreSQL · Qdrant · Redis", "Docker internal network", "어댑터 연결 준비", "idle", icons.database)}
            </div>
          </article>
          <article class="panel rounded-3xl p-5 sm:p-6">
            ${sectionHeading("Provider 구성", "API가 공개한 안전한 설정 정보")}
            <dl class="mt-6 divide-y divide-white/7">
              ${detailRow("AI model", "ai-model")}${detailRow("Embedding", "embedding-model")}${detailRow("Vector DB", "vector-provider")}${detailRow("Backend version", "backend-version")}
            </dl>
          </article>
        </section>

        <section class="mt-5 grid gap-5 xl:grid-cols-[0.8fr_1.2fr]">
          <article class="panel rounded-3xl p-5 sm:p-6">
            ${sectionHeading("API Endpoint", "Frontend 팀 공개 계약")}
            <div class="mt-6 space-y-2 font-mono text-xs">${endpointRow("GET", "/v1/health", "상태")}${endpointRow("POST", "/v1/documents/ingest", "인덱싱")}${endpointRow("POST", "/v1/chat", "채팅")}</div>
            <a href="${apiBaseUrl}/docs" target="_blank" rel="noreferrer" class="mt-5 flex items-center justify-between rounded-2xl border border-mint-300/15 bg-mint-400/5 px-4 py-3 text-xs font-semibold text-mint-300 transition hover:bg-mint-400/10"><span>OpenAPI 명세 열기</span>${icons.arrow}</a>
          </article>
          <article class="panel rounded-3xl p-5 sm:p-6">
            ${sectionHeading("상태 이벤트", "이 브라우저에서 확인한 최근 기록")}
            <div class="mt-6 space-y-4" id="activity-list" aria-live="polite">${activityRow("대시보드가 시작되었습니다", "API 상태 확인을 준비합니다", "방금")}</div>
          </article>
        </section>
      </div>
    </main>
  </div>
`;

const setText = (id: string, value: string) => {
  const element = document.getElementById(id);
  if (element) element.textContent = value;
};

function updateGlobalStatus(ok: boolean, message: string): void {
  const container = document.getElementById("global-status");
  if (!container) return;
  const dot = container.querySelector("span");
  const title = container.querySelector("p");
  dot?.classList.toggle("bg-mint-400", ok);
  dot?.classList.toggle("bg-danger-300", !ok);
  dot?.classList.remove("bg-white/30");
  if (title) {
    title.textContent = message;
    title.className = `text-xs font-semibold ${ok ? "text-mint-300" : "text-danger-300"}`;
  }
}

function setServiceStatus(id: string, message: string, ok: boolean): void {
  const element = document.getElementById(id);
  if (!element) return;
  element.textContent = message;
  element.className = `ml-auto shrink-0 rounded-full border px-2.5 py-1 text-[10px] ${ok ? "border-mint-300/15 bg-mint-400/5 text-mint-300" : "border-danger-300/15 bg-danger-300/5 text-danger-300"}`;
}

function addActivity(title: string, description: string): void {
  const list = document.getElementById("activity-list");
  if (!list) return;
  const time = new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
  list.insertAdjacentHTML("afterbegin", activityRow(title, description, time));
  while (list.children.length > 4) list.lastElementChild?.remove();
}

async function loadHealth(): Promise<void> {
  const refreshButton = document.querySelector<HTMLButtonElement>("#refresh-button");
  if (refreshButton) refreshButton.disabled = true;
  const startedAt = performance.now();

  try {
    const response = await fetch(`${apiBaseUrl}/v1/health`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const health = (await response.json()) as HealthResponse;
    const latency = Math.max(1, Math.round(performance.now() - startedAt));

    setText("latency-value", String(latency));
    setText("chunks-value", health.vector_store.chunks.toLocaleString("ko-KR"));
    setText("projects-value", health.vector_store.projects.toLocaleString("ko-KR"));
    setText("provider-value", health.configuration.ai_provider.toUpperCase());
    setText("ai-model", health.configuration.ai_model);
    setText("embedding-model", health.configuration.embedding_model);
    setText("vector-provider", health.configuration.vector_db_provider.toUpperCase());
    setText("backend-version", `v${health.version}`);
    setText("last-updated", `마지막 동기화 ${new Date().toLocaleString("ko-KR")}`);
    updateGlobalStatus(health.status === "ok", "모든 공개 API 정상");
    setServiceStatus("api-service-status", `정상 · ${latency}ms`, true);
    setServiceStatus("ai-service-status", health.configuration.ai_configured ? "설정 완료" : "키 미설정", health.configuration.ai_configured);
    setServiceStatus("vector-service-status", `${health.configuration.vector_db_provider} · 정상`, true);
    addActivity("API 상태 동기화 완료", `${health.service} · ${latency}ms`);
  } catch (error) {
    const message = error instanceof Error ? error.message : "알 수 없는 오류";
    updateGlobalStatus(false, "API 연결 확인 필요");
    setServiceStatus("api-service-status", "응답 없음", false);
    setText("latency-value", "ERR");
    setText("last-updated", `동기화 실패 · ${new Date().toLocaleTimeString("ko-KR")}`);
    addActivity("API 상태 확인 실패", message);
  } finally {
    if (refreshButton) refreshButton.disabled = false;
  }
}

document.getElementById("refresh-button")?.addEventListener("click", () => void loadHealth());
void loadHealth();
