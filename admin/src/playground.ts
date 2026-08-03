type ModelInfo = {
  model_id: string;
  display_name: string;
  provider: "backendai" | "nvidia" | "groq" | "local";
  location: "internal" | "cloud" | "local";
  deployment_type?: "cloud" | "local" | "remote_server";
  endpoint?: string | null;
  available: boolean;
  is_default: boolean;
  streaming: boolean;
};

type ModelListResponse = {
  models: ModelInfo[];
};

type IndexedProject = {
  project_id: string;
  project_name: string;
  git_short_sha?: string | null;
  git_branch?: string | null;
  active_snapshot_id?: string | null;
  index_status: "not_indexed" | "queued" | "indexing" | "ready" | "failed";
  indexed_at?: string | null;
};

type IndexedProjectListResponse = {
  projects: IndexedProject[];
  total: number;
};

type ChatSource = {
  file: string;
  chunk: string;
  score?: number | null;
};

type ChatResponse = {
  answer: string;
  source: ChatSource[];
  metadata: {
    request_id?: string;
    used_model_id?: string;
    provider?: string;
    timing?: {
      total_ms?: number;
    };
    retrieval?: {
      requested_reasoning_mode?: "auto" | "fast" | "balanced" | "deep";
      reasoning_mode?: "fast" | "balanced" | "deep";
      step_count?: number;
      max_steps?: number;
      stop_reason?: string;
      context_grounded?: boolean;
      degraded?: boolean;
    };
  };
};

type HistoryMessage = {
  role: "user" | "assistant";
  content: string;
};

type AttachedFile = {
  id: string;
  name: string;
  size: number;
  content: string;
};

export function playgroundMarkup(): string {
  return `
    <div class="mx-auto max-w-[1440px] px-4 py-5 sm:px-6 lg:px-8">
      <section class="enter border-b border-white/7 pb-5">
        <div class="mb-3 flex items-center gap-2 text-xs font-semibold tracking-[0.18em] text-mint-300/75"><span class="h-px w-8 bg-mint-300/50"></span> DEVELOPER TOOL</div>
        <div class="flex flex-col gap-3 xl:flex-row xl:items-end xl:justify-between">
          <div><h1 class="text-2xl font-semibold tracking-[-0.04em] text-white sm:text-3xl">sLLM Playground</h1><p class="mt-2 max-w-2xl text-xs leading-5 text-white/45">동일한 <span class="font-mono text-mint-300/80">/v1/chat</span> 계약으로 Local, 특정 서버와 Cloud AI 모델의 RAG 응답을 비교합니다.</p></div>
          <div class="rounded-2xl border border-white/8 bg-white/4 px-4 py-3"><p class="text-[10px] font-semibold tracking-widest text-white/30">PUBLIC API</p><p class="mt-1 font-mono text-xs text-white/65" id="playground-api-base"></p></div>
        </div>
      </section>

      <section class="enter enter-delay-1 mt-4 grid items-start gap-3 xl:grid-cols-[18rem_minmax(0,1fr)]">
        <aside class="panel rounded-2xl p-4 xl:sticky xl:top-20">
          <div class="flex items-start justify-between gap-3">
            <div><h2 class="text-sm font-semibold text-white/90">Indexed Projects</h2><p class="mt-1 text-[10px] leading-4 text-white/35">Backend DB에 등록된 RAG 범위</p></div>
            <button id="playground-project-refresh" type="button" class="rounded-lg border border-white/10 px-2.5 py-1.5 text-[10px] font-semibold text-white/55 transition hover:border-white/20 hover:bg-white/5 disabled:cursor-wait disabled:opacity-50">새로고침</button>
          </div>
          <div class="mt-3 flex items-center justify-between border-y border-white/7 py-2 text-[10px]">
            <span id="playground-project-status" class="text-white/35">목록 불러오는 중</span>
            <span id="playground-project-count" class="font-mono text-mint-300/65">--</span>
          </div>
          <div id="playground-project-list" class="mt-3 max-h-[32rem] space-y-2 overflow-y-auto pr-1" role="listbox" aria-label="인덱싱된 프로젝트">
            <p class="rounded-xl border border-white/7 bg-black/10 p-3 text-xs text-white/30">GET /v1/IngestResponse 응답을 기다립니다.</p>
          </div>
        </aside>

        <div class="min-w-0 space-y-3">
          <article class="panel flex min-h-[46rem] flex-col overflow-hidden rounded-2xl">
            <header class="flex flex-col gap-3 border-b border-white/7 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
              <div class="min-w-0">
                <div class="flex items-center gap-2"><span class="inline-flex size-7 items-center justify-center rounded-lg bg-mint-400/10 text-mint-300"><svg viewBox="0 0 24 24" class="size-4" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3 4.8 7v10L12 21l7.2-4V7L12 3Z"/><path d="m8.5 10 3.5 2 3.5-2M12 12v5"/></svg></span><h2 class="truncate text-sm font-semibold text-white/88">RAG Chat</h2></div>
                <p id="playground-chat-scope" class="mt-1 truncate pl-9 font-mono text-[9px] text-white/30">프로젝트를 선택하세요</p>
              </div>
              <div class="flex flex-wrap items-center gap-2">
                <label class="sr-only" for="playground-reasoning">Agentic 모드</label>
                <select id="playground-reasoning" class="playground-control py-2 text-[11px]" title="Agentic RAG 검색 예산">
                  <option value="auto" selected>Auto · 질문별 자동</option>
                  <option value="fast">Fast · 1단계</option>
                  <option value="balanced">Balanced · 최대 2단계</option>
                  <option value="deep">Deep · 최대 3단계</option>
                </select>
                <label class="sr-only" for="playground-model">모델</label>
                <select id="playground-model" class="playground-control min-w-48 max-w-full py-2 text-[11px]" disabled><option>모델 목록 불러오는 중</option></select>
                <button id="playground-new-chat" type="button" class="inline-flex items-center gap-1.5 rounded-xl border border-white/10 px-3 py-2 text-[10px] font-semibold text-white/55 transition hover:border-white/20 hover:bg-white/5"><svg viewBox="0 0 24 24" class="size-3.5" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 5v14M5 12h14"/></svg>새 대화</button>
              </div>
            </header>

            <div id="playground-model-detail" class="border-b border-white/7 bg-black/10 px-4 py-2 text-[10px] leading-4 text-white/35">GET /v1/models 응답을 기다립니다.</div>
            <input id="playground-project" type="hidden" value="" />
            <input id="playground-session" type="hidden" value="" />

            <div id="playground-chat-log" class="min-h-0 flex-1 space-y-7 overflow-y-auto px-4 py-6 sm:px-8">
              <div id="playground-empty" class="mx-auto flex min-h-[24rem] max-w-2xl flex-col items-center justify-center text-center">
                <span class="inline-flex size-12 items-center justify-center rounded-2xl border border-mint-300/15 bg-mint-400/8 text-mint-300"><svg viewBox="0 0 24 24" class="size-6" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M12 3 4.8 7v10L12 21l7.2-4V7L12 3Z"/><path d="m8.5 10 3.5 2 3.5-2M12 12v5"/></svg></span>
                <h3 class="mt-4 text-lg font-semibold tracking-tight text-white/88">프로젝트 코드에 관해 물어보세요</h3>
                <p class="mt-2 max-w-md text-xs leading-5 text-white/35">선택한 프로젝트를 BGE-M3로 검색하고, 관련 코드와 문서를 근거로 답변합니다.</p>
                <div class="mt-6 grid w-full gap-2 sm:grid-cols-3">
                  <button type="button" data-playground-suggestion="이 프로젝트의 전체 실행 구조를 근거와 함께 설명해줘." class="rounded-xl border border-white/8 bg-white/3 p-3 text-left text-[11px] leading-5 text-white/50 transition hover:border-mint-300/20 hover:bg-mint-400/5 hover:text-white/75">프로젝트 실행 구조</button>
                  <button type="button" data-playground-suggestion="이 프로젝트의 핵심 진입점과 주요 모듈을 알려줘." class="rounded-xl border border-white/8 bg-white/3 p-3 text-left text-[11px] leading-5 text-white/50 transition hover:border-mint-300/20 hover:bg-mint-400/5 hover:text-white/75">핵심 진입점 찾기</button>
                  <button type="button" data-playground-suggestion="이 프로젝트의 오류 처리 흐름을 코드 근거와 함께 설명해줘." class="rounded-xl border border-white/8 bg-white/3 p-3 text-left text-[11px] leading-5 text-white/50 transition hover:border-mint-300/20 hover:bg-mint-400/5 hover:text-white/75">오류 처리 흐름</button>
                </div>
              </div>
            </div>

            <div id="playground-error" class="mx-4 mb-2 hidden rounded-xl border border-danger-300/15 bg-danger-300/5 px-4 py-3 text-xs leading-5 text-danger-300 sm:mx-8"></div>
            <div id="playground-attachments" class="mx-4 hidden flex-wrap gap-2 border-t border-white/7 pt-3 sm:mx-8"></div>

            <form id="playground-form" class="px-4 pb-4 pt-2 sm:px-8 sm:pb-6">
              <input id="playground-file-input" class="hidden" type="file" multiple accept=".txt,.md,.py,.ts,.tsx,.js,.jsx,.json,.yaml,.yml,.toml,.ini,.cfg,.java,.kt,.go,.rs,.c,.h,.cpp,.hpp,.cs,.html,.css,.scss,.sql,.sh,.ps1,.xml" />
              <div class="rounded-3xl border border-white/12 bg-black/20 p-2 shadow-[0_18px_50px_rgba(0,0,0,0.18)] transition focus-within:border-mint-300/30 focus-within:bg-black/25">
                <label class="sr-only" for="playground-prompt">질문</label>
                <textarea id="playground-prompt" class="max-h-52 min-h-14 w-full resize-none bg-transparent px-3 py-2 text-sm leading-6 text-white/78 outline-none placeholder:text-white/25" rows="1" placeholder="선택한 프로젝트에 관해 질문하세요" required></textarea>
                <div class="flex items-center justify-between gap-3 px-1 pb-1">
                  <div class="flex min-w-0 items-center gap-2">
                    <button id="playground-attach" type="button" class="inline-flex size-9 shrink-0 items-center justify-center rounded-full border border-white/10 text-white/50 transition hover:border-white/20 hover:bg-white/7 hover:text-white/75" title="코드 또는 텍스트 파일 첨부" aria-label="코드 또는 텍스트 파일 첨부"><svg viewBox="0 0 24 24" class="size-4" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m20.5 11.5-8.2 8.2a5 5 0 0 1-7.1-7.1l9-9a3.5 3.5 0 0 1 5 5l-9.1 9.1a2 2 0 0 1-2.8-2.8l8.2-8.2"/></svg></button>
                    <span id="playground-attachment-summary" class="truncate text-[10px] text-white/28">텍스트·코드 파일 · 합계 5MB 이하</span>
                  </div>
                  <div class="flex shrink-0 items-center gap-2">
                    <span id="playground-request-status" class="text-[10px] text-white/30">준비</span>
                    <button id="playground-submit" type="submit" class="inline-flex size-9 items-center justify-center rounded-full bg-mint-400 text-ink-950 transition hover:bg-mint-300 disabled:cursor-wait disabled:opacity-40" title="메시지 보내기" aria-label="메시지 보내기"><svg viewBox="0 0 24 24" class="size-4" fill="none" stroke="currentColor" stroke-width="2"><path d="m6 12 6-6 6 6M12 6v12"/></svg></button>
                  </div>
                </div>
              </div>
              <p class="mt-2 text-center text-[9px] text-white/22">Enter 전송 · Shift+Enter 줄바꿈 · 첨부파일은 context로 전달 · stream=false</p>
            </form>
          </article>

          <details class="panel rounded-2xl p-4">
            <summary class="cursor-pointer text-sm font-semibold text-white/70">전송 JSON 확인</summary>
            <pre id="playground-request-preview" class="mt-5 overflow-x-auto whitespace-pre-wrap rounded-2xl border border-white/7 bg-black/20 p-4 font-mono text-[11px] leading-5 text-white/45">{}</pre>
          </details>
        </div>
      </section>
    </div>`;
}

const requiredElement = <T extends HTMLElement>(id: string): T => {
  const element = document.getElementById(id);
  if (!element) throw new Error(`Missing playground element: ${id}`);
  return element as T;
};

function errorMessage(data: unknown, fallback: string): string {
  if (typeof data === "object" && data !== null && "detail" in data) {
    const detail = (data as { detail: unknown }).detail;
    return typeof detail === "string" ? detail : JSON.stringify(detail);
  }
  return fallback;
}

export function startPlayground(apiBaseUrl: string): void {
  const form = requiredElement<HTMLFormElement>("playground-form");
  const reasoningSelect = requiredElement<HTMLSelectElement>("playground-reasoning");
  const modelSelect = requiredElement<HTMLSelectElement>("playground-model");
  const modelDetail = requiredElement<HTMLDivElement>("playground-model-detail");
  const projectInput = requiredElement<HTMLInputElement>("playground-project");
  const projectList = requiredElement<HTMLDivElement>("playground-project-list");
  const projectStatus = requiredElement<HTMLSpanElement>("playground-project-status");
  const projectCount = requiredElement<HTMLSpanElement>("playground-project-count");
  const projectRefresh = requiredElement<HTMLButtonElement>("playground-project-refresh");
  const sessionInput = requiredElement<HTMLInputElement>("playground-session");
  const promptInput = requiredElement<HTMLTextAreaElement>("playground-prompt");
  const submitButton = requiredElement<HTMLButtonElement>("playground-submit");
  const requestStatus = requiredElement<HTMLSpanElement>("playground-request-status");
  const requestPreview = requiredElement<HTMLPreElement>("playground-request-preview");
  const emptyState = requiredElement<HTMLDivElement>("playground-empty");
  const chatLog = requiredElement<HTMLDivElement>("playground-chat-log");
  const chatScope = requiredElement<HTMLParagraphElement>("playground-chat-scope");
  const errorBox = requiredElement<HTMLDivElement>("playground-error");
  const newChatButton = requiredElement<HTMLButtonElement>("playground-new-chat");
  const attachButton = requiredElement<HTMLButtonElement>("playground-attach");
  const fileInput = requiredElement<HTMLInputElement>("playground-file-input");
  const attachmentsContainer = requiredElement<HTMLDivElement>("playground-attachments");
  const attachmentSummary = requiredElement<HTMLSpanElement>("playground-attachment-summary");
  requiredElement<HTMLElement>("playground-api-base").textContent = apiBaseUrl;

  let models: ModelInfo[] = [];
  let projects: IndexedProject[] = [];
  let history: HistoryMessage[] = [];
  let attachments: AttachedFile[] = [];
  let requestInFlight = false;
  const maxAttachmentBytes = 5 * 1024 * 1024;
  const maxAttachmentFiles = 20;
  const createSessionId = () => `playground-${crypto.randomUUID()}`;
  sessionInput.value = createSessionId();
  const storedReasoningMode = localStorage.getItem("vision-playground-reasoning-mode");
  if (storedReasoningMode && ["auto", "fast", "balanced", "deep"].includes(storedReasoningMode)) {
    reasoningSelect.value = storedReasoningMode;
  }
  const providerLabel = (provider: string): string => ({
    backendai: "OLLAMA",
    nvidia: "NVIDIA",
    groq: "GROQ",
    local: "LOCAL",
  })[provider] || provider.toUpperCase();
  const deploymentLabel = (model: ModelInfo): string => {
    const deployment = model.deployment_type || (
      model.location === "internal" ? "remote_server" : model.location
    );
    return {
      cloud: "Cloud",
      local: "Local",
      remote_server: "특정 서버",
    }[deployment];
  };

  const updateModelDetail = () => {
    const selected = models.find((model) => model.model_id === modelSelect.value);
    if (!selected) return;
    modelDetail.textContent = `${providerLabel(selected.provider)} · ${deploymentLabel(selected)}${selected.endpoint ? ` · ${selected.endpoint}` : ""} · ${selected.available ? "사용 가능" : "현재 응답 불가"}${selected.is_default ? " · 기본 모델" : ""}`;
    modelDetail.className = `border-b px-4 py-2 text-[10px] leading-4 ${selected.available ? "border-mint-300/10 bg-mint-400/4 text-mint-300/70" : "border-amber-300/10 bg-amber-300/4 text-amber-300/75"}`;
  };

  const loadModels = async () => {
    try {
      const response = await fetch(`${apiBaseUrl}/v1/models`, {
        headers: {
          Accept: "application/json",
          "X-Client-Type": "admin-playground",
        },
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = (await response.json()) as ModelListResponse;
      models = data.models;
      modelSelect.replaceChildren();
      for (const model of models) {
        const option = document.createElement("option");
        option.value = model.model_id;
        option.textContent = `${model.display_name} — ${model.available ? "online" : "unavailable"}`;
        option.disabled = !model.available;
        modelSelect.append(option);
      }
      const preferred = models.find((model) => model.is_default && model.available)
        || models.find((model) => model.available);
      if (!preferred) throw new Error("사용 가능한 모델 정의가 없습니다.");
      modelSelect.value = preferred.model_id;
      modelSelect.disabled = false;
      updateModelDetail();
    } catch (error) {
      modelSelect.replaceChildren(new Option("모델 목록 조회 실패", ""));
      modelDetail.textContent = error instanceof Error ? error.message : "모델 목록 조회 실패";
      modelDetail.className = "border-b border-danger-300/10 bg-danger-300/5 px-4 py-2 text-[10px] text-danger-300";
    }
  };

  const projectStatusClass = (status: IndexedProject["index_status"]): string => ({
    ready: "border-mint-300/15 bg-mint-400/7 text-mint-300",
    indexing: "border-amber-300/15 bg-amber-300/7 text-amber-300",
    queued: "border-sky-300/15 bg-sky-300/7 text-sky-300",
    failed: "border-danger-300/15 bg-danger-300/7 text-danger-300",
    not_indexed: "border-white/10 bg-white/5 text-white/40",
  })[status];

  const renderProjects = () => {
    projectList.replaceChildren();
    const selectedProjectId = projectInput.value;
    const readyCount = projects.filter((project) => project.index_status === "ready").length;
    projectCount.textContent = `${readyCount}/${projects.length} ready`;
    projectStatus.textContent = projects.length > 0
      ? `${projects.length}개 프로젝트 · 클릭하여 RAG 범위 선택`
      : "등록된 프로젝트가 없습니다.";

    if (projects.length === 0) {
      const empty = document.createElement("p");
      empty.className = "rounded-xl border border-white/7 bg-black/10 p-3 text-xs leading-5 text-white/30";
      empty.textContent = "Backend DB에 등록된 프로젝트가 없습니다.";
      projectList.append(empty);
      return;
    }

    for (const project of projects) {
      const selectable = project.index_status === "ready";
      const selected = project.project_id === selectedProjectId;
      const item = document.createElement("button");
      item.type = "button";
      item.disabled = !selectable;
      item.dataset.projectId = project.project_id;
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", String(selected));
      item.className = `w-full rounded-xl border p-3 text-left transition ${
        selected
          ? "border-mint-300/30 bg-mint-400/10 shadow-[0_0_0_1px_rgba(110,231,183,0.08)]"
          : "border-white/7 bg-black/10 hover:border-white/15 hover:bg-white/4"
      } ${selectable ? "" : "cursor-not-allowed opacity-55"}`;

      const heading = document.createElement("div");
      heading.className = "flex items-start justify-between gap-2";
      const names = document.createElement("div");
      names.className = "min-w-0";
      const name = document.createElement("p");
      name.className = "truncate text-xs font-semibold text-white/78";
      name.textContent = project.project_name;
      const id = document.createElement("p");
      id.className = "mt-1 truncate font-mono text-[9px] text-white/28";
      id.textContent = project.project_id;
      names.append(name, id);
      const status = document.createElement("span");
      status.className = `shrink-0 rounded-full border px-2 py-1 text-[8px] font-semibold uppercase ${projectStatusClass(project.index_status)}`;
      status.textContent = project.index_status;
      heading.append(names, status);

      const version = document.createElement("p");
      version.className = "mt-2 truncate font-mono text-[9px] text-white/35";
      version.textContent = project.git_short_sha
        ? `${project.git_branch || "git"}@${project.git_short_sha}`
        : project.active_snapshot_id || "snapshot 없음";
      item.append(heading, version);
      projectList.append(item);
    }
  };

  const selectProject = (projectId: string, resetSession = true) => {
    if (requestInFlight) return;
    const project = projects.find((item) => item.project_id === projectId);
    if (!project || project.index_status !== "ready") return;
    const changed = projectInput.value !== project.project_id;
    projectInput.value = project.project_id;
    chatScope.textContent = `${project.project_name} · ${project.project_id}`;
    localStorage.setItem("vision-playground-project-id", project.project_id);
    if (resetSession && changed) {
      sessionInput.value = createSessionId();
      history = [];
      attachments = [];
      attachmentsContainer.replaceChildren();
      attachmentsContainer.classList.add("hidden");
      attachmentSummary.textContent = "텍스트·코드 파일 · 합계 5MB 이하";
      chatLog.replaceChildren(emptyState);
      emptyState.classList.remove("hidden");
      errorBox.classList.add("hidden");
      requestPreview.textContent = "{}";
    }
    renderProjects();
  };

  const loadProjects = async () => {
    projectRefresh.disabled = true;
    projectRefresh.textContent = "조회 중";
    projectStatus.textContent = "GET /v1/IngestResponse 요청 중";
    try {
      const response = await fetch(`${apiBaseUrl}/v1/IngestResponse`, {
        headers: {
          Accept: "application/json",
          "X-Client-Type": "admin-playground",
        },
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = (await response.json()) as IndexedProjectListResponse;
      projects = data.projects;
      const storedProjectId = localStorage.getItem("vision-playground-project-id");
      const preferred = projects.find(
        (project) => project.project_id === storedProjectId && project.index_status === "ready",
      ) || projects.find(
        (project) => project.index_status === "ready" && Boolean(project.active_snapshot_id),
      ) || projects.find(
        (project) => project.index_status === "ready" && project.project_id !== "default",
      ) || projects.find((project) => project.index_status === "ready");
      if (preferred) {
        projectInput.value = preferred.project_id;
        chatScope.textContent = `${preferred.project_name} · ${preferred.project_id}`;
        localStorage.setItem("vision-playground-project-id", preferred.project_id);
      } else {
        projectInput.value = "";
        chatScope.textContent = "ready 프로젝트가 없습니다";
      }
      renderProjects();
    } catch (error) {
      projects = [];
      projectInput.value = "";
      chatScope.textContent = "프로젝트 목록 조회 실패";
      projectCount.textContent = "error";
      projectStatus.textContent = error instanceof Error ? error.message : "프로젝트 목록 조회 실패";
      projectList.replaceChildren();
      const message = document.createElement("p");
      message.className = "rounded-xl border border-danger-300/15 bg-danger-300/5 p-3 text-xs leading-5 text-danger-300";
      message.textContent = "프로젝트 목록을 불러오지 못했습니다. API 연결을 확인하세요.";
      projectList.append(message);
    } finally {
      projectRefresh.disabled = false;
      projectRefresh.textContent = "새로고침";
    }
  };

  const formatBytes = (value: number): string => {
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / (1024 * 1024)).toFixed(2)} MB`;
  };

  const setRequestStatus = (label: string, tone: "idle" | "busy" | "success" | "error") => {
    requestStatus.textContent = label;
    requestStatus.className = ({
      idle: "text-[10px] text-white/30",
      busy: "text-[10px] text-amber-300/75",
      success: "text-[10px] text-mint-300/75",
      error: "text-[10px] text-danger-300/80",
    })[tone];
  };

  const scrollChatToBottom = () => {
    requestAnimationFrame(() => {
      chatLog.scrollTop = chatLog.scrollHeight;
    });
  };

  const renderAttachments = () => {
    attachmentsContainer.replaceChildren();
    if (attachments.length === 0) {
      attachmentsContainer.classList.add("hidden");
      attachmentSummary.textContent = "텍스트·코드 파일 · 합계 5MB 이하";
      return;
    }
    attachmentsContainer.classList.remove("hidden");
    attachmentsContainer.classList.add("flex");
    const totalBytes = attachments.reduce((sum, file) => sum + file.size, 0);
    attachmentSummary.textContent = `${attachments.length}개 · ${formatBytes(totalBytes)}`;
    for (const file of attachments) {
      const chip = document.createElement("span");
      chip.className = "inline-flex max-w-full items-center gap-2 rounded-xl border border-white/10 bg-black/15 px-3 py-2 text-[10px] text-white/55";
      const name = document.createElement("span");
      name.className = "max-w-48 truncate";
      name.textContent = `${file.name} · ${formatBytes(file.size)}`;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.dataset.attachmentId = file.id;
      remove.className = "text-white/30 transition hover:text-danger-300";
      remove.setAttribute("aria-label", `${file.name} 첨부 제거`);
      remove.textContent = "×";
      chip.append(name, remove);
      attachmentsContainer.append(chip);
    }
  };

  const resetConversation = () => {
    if (requestInFlight) return;
    history = [];
    attachments = [];
    sessionInput.value = createSessionId();
    chatLog.replaceChildren(emptyState);
    emptyState.classList.remove("hidden");
    errorBox.classList.add("hidden");
    requestPreview.textContent = "{}";
    promptInput.value = "";
    promptInput.style.height = "";
    renderAttachments();
    setRequestStatus("준비", "idle");
  };

  const appendUserMessage = (message: string, files: AttachedFile[]) => {
    emptyState.classList.add("hidden");
    const row = document.createElement("div");
    row.className = "flex justify-end";
    const bubble = document.createElement("article");
    bubble.className = "max-w-[88%] rounded-3xl rounded-br-lg bg-white/9 px-4 py-3 text-sm leading-6 text-white/78 sm:max-w-[76%]";
    const content = document.createElement("p");
    content.className = "whitespace-pre-wrap";
    content.textContent = message;
    bubble.append(content);
    if (files.length > 0) {
      const fileList = document.createElement("div");
      fileList.className = "mt-3 flex flex-wrap gap-1.5 border-t border-white/8 pt-2";
      for (const file of files) {
        const fileChip = document.createElement("span");
        fileChip.className = "rounded-lg bg-black/15 px-2 py-1 font-mono text-[9px] text-white/40";
        fileChip.textContent = file.name;
        fileList.append(fileChip);
      }
      bubble.append(fileList);
    }
    row.append(bubble);
    chatLog.append(row);
    scrollChatToBottom();
  };

  const appendPendingMessage = (): HTMLElement => {
    const row = document.createElement("div");
    row.className = "flex items-start gap-3";
    const avatar = document.createElement("span");
    avatar.className = "mt-0.5 inline-flex size-7 shrink-0 items-center justify-center rounded-lg bg-mint-400/10 font-mono text-[9px] font-bold text-mint-300";
    avatar.textContent = "AI";
    const content = document.createElement("div");
    content.className = "flex items-center gap-2 pt-1.5 text-xs text-white/35";
    const dots = document.createElement("span");
    dots.className = "font-mono tracking-[0.25em] text-mint-300/60";
    dots.textContent = "•••";
    const label = document.createElement("span");
    label.textContent = "프로젝트를 검색하고 답변을 생성하는 중";
    content.append(dots, label);
    row.append(avatar, content);
    chatLog.append(row);
    scrollChatToBottom();
    return row;
  };

  const createSources = (items: ChatSource[]): HTMLElement | null => {
    if (items.length === 0) return null;
    const details = document.createElement("details");
    details.className = "mt-4 rounded-2xl border border-white/8 bg-black/10";
    const summary = document.createElement("summary");
    summary.className = "cursor-pointer px-3 py-2.5 text-[10px] font-semibold text-mint-300/70";
    summary.textContent = `RAG 근거 ${items.length}개`;
    const list = document.createElement("div");
    list.className = "space-y-2 border-t border-white/7 p-2.5";
    items.forEach((source, index) => {
      const card = document.createElement("article");
      card.className = "rounded-xl border border-white/7 bg-white/3 p-3";
      const heading = document.createElement("div");
      heading.className = "flex items-start justify-between gap-3";
      const path = document.createElement("p");
      path.className = "min-w-0 truncate font-mono text-[10px] text-mint-300/70";
      path.textContent = `[${index + 1}] ${source.file}`;
      const score = document.createElement("span");
      score.className = "shrink-0 font-mono text-[9px] text-white/30";
      score.textContent = source.score === null || source.score === undefined
        ? "score -"
        : `score ${source.score.toFixed(4)}`;
      heading.append(path, score);
      const excerpt = document.createElement("p");
      excerpt.className = "mt-2 line-clamp-5 whitespace-pre-wrap text-[10px] leading-5 text-white/38";
      excerpt.textContent = source.chunk;
      card.append(heading, excerpt);
      list.append(card);
    });
    details.append(summary, list);
    return details;
  };

  const appendAssistantMessage = (chat: ChatResponse, elapsed: number) => {
    const row = document.createElement("div");
    row.className = "flex items-start gap-3";
    const avatar = document.createElement("span");
    avatar.className = "mt-0.5 inline-flex size-7 shrink-0 items-center justify-center rounded-lg bg-mint-400/10 font-mono text-[9px] font-bold text-mint-300";
    avatar.textContent = "AI";
    const body = document.createElement("article");
    body.className = "min-w-0 max-w-3xl flex-1";
    const answer = document.createElement("p");
    answer.className = "whitespace-pre-wrap text-sm leading-7 text-white/74";
    answer.textContent = chat.answer;
    body.append(answer);
    const sourceDetails = createSources(Array.isArray(chat.source) ? chat.source : []);
    if (sourceDetails) body.append(sourceDetails);

    const metadata = chat.metadata || {};
    const provider = metadata.provider || "unknown";
    const model = metadata.used_model_id || "default";
    const serverMs = metadata.timing?.total_ms;
    const retrieval = metadata.retrieval;
    const footer = document.createElement("div");
    footer.className = "mt-3 flex flex-wrap items-center gap-3 text-[9px] text-white/25";
    const meta = document.createElement("span");
    meta.className = "font-mono";
    const selectedMode = retrieval?.requested_reasoning_mode === "auto"
      ? `auto→${retrieval.reasoning_mode}`
      : retrieval?.reasoning_mode;
    const agentic = selectedMode
      ? ` · ${selectedMode} ${retrieval?.step_count ?? "-"}/${retrieval?.max_steps ?? "-"}${retrieval?.context_grounded ? " · context" : ""}${retrieval?.degraded ? " · degraded" : ""}`
      : "";
    meta.textContent = `${providerLabel(provider)} · ${model}${agentic} · server ${serverMs ?? "-"}ms · total ${elapsed}ms`;
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "transition hover:text-white/60";
    copy.textContent = "답변 복사";
    copy.addEventListener("click", () => {
      void navigator.clipboard.writeText(chat.answer);
      copy.textContent = "복사됨";
      window.setTimeout(() => {
        copy.textContent = "답변 복사";
      }, 1200);
    });
    footer.append(meta, copy);
    body.append(footer);
    row.append(avatar, body);
    chatLog.append(row);
    scrollChatToBottom();
  };

  const runModel = async () => {
    if (requestInFlight) return;
    const question = promptInput.value.trim();
    const selectedAttachments = [...attachments];
    const attachmentContext = selectedAttachments.map(
      (file) => `--- 첨부 파일: ${file.name} ---\n${file.content}`,
    ).join("\n\n");
    if (new TextEncoder().encode(attachmentContext).byteLength > maxAttachmentBytes) {
      errorBox.textContent = "파일명과 구분자를 포함한 첨부 context는 5MB를 초과할 수 없습니다.";
      errorBox.classList.remove("hidden");
      return;
    }
    const clientRequestId = `playground-${crypto.randomUUID()}`;
    const payload = {
      schema_version: "1.0" as const,
      client_request_id: clientRequestId,
      project_id: projectInput.value.trim(),
      session_id: sessionInput.value.trim(),
      model_id: modelSelect.value,
      message: question,
      reasoning_mode: reasoningSelect.value as "auto" | "fast" | "balanced" | "deep",
      history: history.slice(-20),
      context: attachmentContext,
      stream: false,
    };
    requestPreview.textContent = JSON.stringify({
      ...payload,
      context: selectedAttachments.length > 0
        ? `[${selectedAttachments.length}개 첨부파일 · ${formatBytes(selectedAttachments.reduce((sum, file) => sum + file.size, 0))}]`
        : "",
    }, null, 2);
    if (!payload.project_id || !payload.session_id || !payload.message || !payload.model_id) {
      errorBox.textContent = payload.project_id
        ? "사용 가능한 모델과 질문을 확인하세요."
        : "왼쪽 Sidebar에서 ready 프로젝트를 선택하세요.";
      errorBox.classList.remove("hidden");
      return;
    }

    requestInFlight = true;
    submitButton.disabled = true;
    attachButton.disabled = true;
    modelSelect.disabled = true;
    reasoningSelect.disabled = true;
    errorBox.classList.add("hidden");
    setRequestStatus("RAG 검색 중", "busy");
    appendUserMessage(question, selectedAttachments);
    const pending = appendPendingMessage();
    promptInput.value = "";
    promptInput.style.height = "";
    const startedAt = performance.now();

    try {
      const response = await fetch(`${apiBaseUrl}/v1/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          "X-Client-Type": "admin-playground",
          "X-Request-ID": clientRequestId,
        },
        body: JSON.stringify(payload),
      });
      const data = await response.json().catch(() => null) as ChatResponse | { detail?: unknown } | null;
      if (!response.ok) throw new Error(errorMessage(data, `HTTP ${response.status}`));
      const chat = data as ChatResponse;
      pending.remove();
      history.push(
        { role: "user", content: question },
        { role: "assistant", content: chat.answer },
      );
      history = history.slice(-20);
      attachments = [];
      renderAttachments();
      appendAssistantMessage(chat, Math.round(performance.now() - startedAt));
      setRequestStatus("완료", "success");
    } catch (error) {
      pending.remove();
      const message = error instanceof Error ? error.message : "알 수 없는 요청 오류";
      errorBox.textContent = message;
      errorBox.classList.remove("hidden");
      promptInput.value = question;
      setRequestStatus("실패", "error");
    } finally {
      requestInFlight = false;
      submitButton.disabled = false;
      attachButton.disabled = false;
      modelSelect.disabled = false;
      reasoningSelect.disabled = false;
      promptInput.focus();
    }
  };

  modelSelect.addEventListener("change", updateModelDetail);
  reasoningSelect.addEventListener("change", () => {
    localStorage.setItem("vision-playground-reasoning-mode", reasoningSelect.value);
  });
  projectList.addEventListener("click", (event) => {
    const target = event.target instanceof Element
      ? event.target.closest<HTMLButtonElement>("button[data-project-id]")
      : null;
    if (target?.dataset.projectId) selectProject(target.dataset.projectId);
  });
  projectRefresh.addEventListener("click", () => {
    void loadProjects();
  });
  newChatButton.addEventListener("click", resetConversation);
  attachButton.addEventListener("click", () => {
    fileInput.click();
  });
  fileInput.addEventListener("change", async () => {
    const selectedFiles = Array.from(fileInput.files || []);
    errorBox.classList.add("hidden");
    try {
      if (attachments.length + selectedFiles.length > maxAttachmentFiles) {
        throw new Error(`첨부파일은 최대 ${maxAttachmentFiles}개까지 선택할 수 있습니다.`);
      }
      let totalBytes = attachments.reduce((sum, file) => sum + file.size, 0);
      for (const file of selectedFiles) {
        if (totalBytes + file.size > maxAttachmentBytes) {
          throw new Error("첨부파일 합계는 5MB를 초과할 수 없습니다.");
        }
        const content = await file.text();
        attachments.push({
          id: crypto.randomUUID(),
          name: file.name,
          size: file.size,
          content,
        });
        totalBytes += file.size;
      }
      renderAttachments();
    } catch (error) {
      errorBox.textContent = error instanceof Error ? error.message : "첨부파일을 읽지 못했습니다.";
      errorBox.classList.remove("hidden");
    } finally {
      fileInput.value = "";
    }
  });
  attachmentsContainer.addEventListener("click", (event) => {
    const target = event.target instanceof Element
      ? event.target.closest<HTMLButtonElement>("button[data-attachment-id]")
      : null;
    if (!target?.dataset.attachmentId) return;
    attachments = attachments.filter((file) => file.id !== target.dataset.attachmentId);
    renderAttachments();
  });
  chatLog.addEventListener("click", (event) => {
    const target = event.target instanceof Element
      ? event.target.closest<HTMLButtonElement>("button[data-playground-suggestion]")
      : null;
    if (!target?.dataset.playgroundSuggestion) return;
    promptInput.value = target.dataset.playgroundSuggestion;
    promptInput.dispatchEvent(new Event("input"));
    promptInput.focus();
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void runModel();
  });
  promptInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  promptInput.addEventListener("input", () => {
    promptInput.style.height = "auto";
    promptInput.style.height = `${Math.min(promptInput.scrollHeight, 208)}px`;
  });
  void loadModels();
  void loadProjects();
}
