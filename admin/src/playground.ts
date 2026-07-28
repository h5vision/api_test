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
  };
};

export function playgroundMarkup(): string {
  return `
    <div class="mx-auto max-w-[1280px] px-4 py-5 sm:px-6 lg:px-8">
      <section class="enter border-b border-white/7 pb-5">
        <div class="mb-3 flex items-center gap-2 text-xs font-semibold tracking-[0.18em] text-mint-300/75"><span class="h-px w-8 bg-mint-300/50"></span> DEVELOPER TOOL</div>
        <div class="flex flex-col gap-3 xl:flex-row xl:items-end xl:justify-between">
          <div><h1 class="text-2xl font-semibold tracking-[-0.04em] text-white sm:text-3xl">sLLM Playground</h1><p class="mt-2 max-w-2xl text-xs leading-5 text-white/45">동일한 <span class="font-mono text-mint-300/80">/v1/chat</span> 계약으로 Local, 특정 서버와 Cloud AI 모델의 RAG 응답을 비교합니다.</p></div>
          <div class="rounded-2xl border border-white/8 bg-white/4 px-4 py-3"><p class="text-[10px] font-semibold tracking-widest text-white/30">PUBLIC API</p><p class="mt-1 font-mono text-xs text-white/65" id="playground-api-base"></p></div>
        </div>
      </section>

      <section class="enter enter-delay-1 mt-4 grid gap-3 xl:grid-cols-[0.72fr_1.28fr]">
        <article class="panel rounded-2xl p-4">
          <div><h2 class="text-base font-semibold text-white/90">실행 설정</h2><p class="mt-1 text-xs text-white/35">모델과 RAG 프로젝트 범위</p></div>
          <div class="mt-3 space-y-3">
            <label class="block"><span class="text-xs font-medium text-white/55">모델</span><select id="playground-model" class="playground-control mt-2 w-full" disabled><option>모델 목록 불러오는 중</option></select></label>
            <div id="playground-model-detail" class="rounded-2xl border border-white/7 bg-black/10 p-3.5 text-xs leading-5 text-white/40">GET /v1/models 응답을 기다립니다.</div>
            <label class="block"><span class="text-xs font-medium text-white/55">Project ID</span><input id="playground-project" class="playground-control mt-2 w-full" value="Vision" maxlength="255" /></label>
            <label class="block"><span class="text-xs font-medium text-white/55">Session ID</span><input id="playground-session" class="playground-control mt-2 w-full" value="Vision" maxlength="255" /><span class="mt-1.5 block text-[10px] text-white/28">VS Code에서는 프로젝트 폴더명을 사용합니다.</span></label>
            <label class="block"><span class="text-xs font-medium text-white/55">검색 청크 수</span><input id="playground-top-k" class="playground-control mt-2 w-full" type="number" value="5" min="1" max="20" /></label>
          </div>
        </article>

        <article class="panel rounded-2xl p-4">
          <div class="flex items-start justify-between gap-4"><div><h2 class="text-base font-semibold text-white/90">Prompt</h2><p class="mt-1 text-xs text-white/35">Ctrl + Enter로 실행할 수 있습니다.</p></div><span id="playground-request-status" class="rounded-full border border-white/8 px-2.5 py-1 text-[10px] text-white/38">준비</span></div>
          <form id="playground-form" class="mt-3">
            <label class="sr-only" for="playground-prompt">질문</label>
            <textarea id="playground-prompt" class="playground-control min-h-48 w-full resize-y leading-6" placeholder="프로젝트 코드에 관해 질문하세요." required>이 프로젝트의 실행 구조를 근거와 함께 설명해줘.</textarea>
            <div class="mt-4 flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between"><p class="text-[10px] text-white/28">stream=false · history=[] · RAG sources 포함</p><button id="playground-submit" type="submit" class="rounded-xl bg-mint-400 px-5 py-2.5 text-xs font-bold text-ink-950 transition hover:bg-mint-300 disabled:cursor-wait disabled:opacity-50">모델 실행</button></div>
          </form>
        </article>
      </section>

      <section class="mt-3 grid gap-3 xl:grid-cols-[1.2fr_0.8fr]">
        <article class="panel min-h-64 rounded-2xl p-4">
          <div class="flex items-start justify-between gap-4"><div><h2 class="text-base font-semibold text-white/90">Model Response</h2><p id="playground-response-meta" class="mt-1 font-mono text-[10px] text-white/30">아직 실행하지 않았습니다.</p></div><button id="playground-copy" class="hidden rounded-xl border border-white/10 px-3 py-2 text-[10px] text-white/55 transition hover:bg-white/5" type="button">답변 복사</button></div>
          <div id="playground-empty" class="grid min-h-52 place-items-center text-center"><div><p class="font-mono text-sm text-white/25">await model.run()</p><p class="mt-2 text-xs text-white/25">실행 결과가 여기에 표시됩니다.</p></div></div>
          <div id="playground-result" class="mt-6 hidden"><p id="playground-answer" class="whitespace-pre-wrap text-sm leading-7 text-white/75"></p></div>
          <div id="playground-error" class="mt-6 hidden rounded-2xl border border-danger-300/15 bg-danger-300/5 p-4 text-sm leading-6 text-danger-300"></div>
        </article>

        <article class="panel rounded-2xl p-4">
          <div><h2 class="text-base font-semibold text-white/90">RAG Sources</h2><p class="mt-1 text-xs text-white/35">Qdrant 검색 근거와 유사도</p></div>
          <div id="playground-sources" class="mt-6 space-y-3"><p class="text-xs text-white/25">응답을 실행하면 sources[]가 표시됩니다.</p></div>
        </article>
      </section>

      <details class="panel mt-3 rounded-2xl p-4">
        <summary class="cursor-pointer text-sm font-semibold text-white/70">전송 JSON 확인</summary>
        <pre id="playground-request-preview" class="mt-5 overflow-x-auto whitespace-pre-wrap rounded-2xl border border-white/7 bg-black/20 p-4 font-mono text-[11px] leading-5 text-white/45">{}</pre>
      </details>
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
  const modelSelect = requiredElement<HTMLSelectElement>("playground-model");
  const modelDetail = requiredElement<HTMLDivElement>("playground-model-detail");
  const projectInput = requiredElement<HTMLInputElement>("playground-project");
  const sessionInput = requiredElement<HTMLInputElement>("playground-session");
  const topKInput = requiredElement<HTMLInputElement>("playground-top-k");
  const promptInput = requiredElement<HTMLTextAreaElement>("playground-prompt");
  const submitButton = requiredElement<HTMLButtonElement>("playground-submit");
  const requestStatus = requiredElement<HTMLSpanElement>("playground-request-status");
  const requestPreview = requiredElement<HTMLPreElement>("playground-request-preview");
  const responseMeta = requiredElement<HTMLParagraphElement>("playground-response-meta");
  const emptyState = requiredElement<HTMLDivElement>("playground-empty");
  const result = requiredElement<HTMLDivElement>("playground-result");
  const answer = requiredElement<HTMLParagraphElement>("playground-answer");
  const errorBox = requiredElement<HTMLDivElement>("playground-error");
  const sources = requiredElement<HTMLDivElement>("playground-sources");
  const copyButton = requiredElement<HTMLButtonElement>("playground-copy");
  requiredElement<HTMLElement>("playground-api-base").textContent = apiBaseUrl;

  let models: ModelInfo[] = [];
  let lastAnswer = "";
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
    modelDetail.className = `rounded-2xl border p-3.5 text-xs leading-5 ${selected.available ? "border-mint-300/15 bg-mint-400/5 text-mint-300" : "border-amber-300/15 bg-amber-300/5 text-amber-300"}`;
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
        modelSelect.append(option);
      }
      const preferred = models.find((model) => model.is_default) || models[0];
      if (!preferred) throw new Error("사용 가능한 모델 정의가 없습니다.");
      modelSelect.value = preferred.model_id;
      modelSelect.disabled = false;
      updateModelDetail();
    } catch (error) {
      modelSelect.replaceChildren(new Option("모델 목록 조회 실패", ""));
      modelDetail.textContent = error instanceof Error ? error.message : "모델 목록 조회 실패";
      modelDetail.className = "rounded-2xl border border-danger-300/15 bg-danger-300/5 p-3.5 text-xs text-danger-300";
    }
  };

  const renderSources = (items: ChatSource[]) => {
    sources.replaceChildren();
    if (items.length === 0) {
      const message = document.createElement("p");
      message.className = "text-xs text-white/25";
      message.textContent = "검색된 source가 없습니다.";
      sources.append(message);
      return;
    }
    items.forEach((source, index) => {
      const card = document.createElement("article");
      card.className = "rounded-2xl border border-white/7 bg-black/10 p-4";
      const heading = document.createElement("div");
      heading.className = "flex items-start justify-between gap-3";
      const path = document.createElement("p");
      path.className = "min-w-0 truncate font-mono text-[11px] text-mint-300/75";
      path.textContent = `${index + 1}. ${source.file}`;
      const score = document.createElement("span");
      score.className = "shrink-0 font-mono text-[10px] text-white/35";
      score.textContent = source.score === null || source.score === undefined
        ? "-"
        : source.score.toFixed(4);
      heading.append(path, score);
      const excerpt = document.createElement("p");
      excerpt.className = "mt-3 line-clamp-4 whitespace-pre-wrap text-xs leading-5 text-white/42";
      excerpt.textContent = source.chunk;
      card.append(heading, excerpt);
      sources.append(card);
    });
  };

  const runModel = async () => {
    const clientRequestId = `playground-${crypto.randomUUID()}`;
    const payload = {
      schema_version: "1.0" as const,
      client_request_id: clientRequestId,
      project_id: projectInput.value.trim(),
      session_id: sessionInput.value.trim(),
      model_id: modelSelect.value,
      message: promptInput.value.trim(),
      top_k: Number(topKInput.value),
      history: [],
      stream: false,
    };
    requestPreview.textContent = JSON.stringify(payload, null, 2);
    if (!payload.project_id || !payload.session_id || !payload.message || !payload.model_id) {
      errorBox.textContent = "모델, project_id, session_id와 질문을 모두 입력하세요.";
      errorBox.classList.remove("hidden");
      return;
    }

    submitButton.disabled = true;
    submitButton.textContent = "실행 중…";
    requestStatus.textContent = "요청 중";
    requestStatus.className = "rounded-full border border-amber-300/15 bg-amber-300/5 px-2.5 py-1 text-[10px] text-amber-300";
    emptyState.classList.add("hidden");
    result.classList.add("hidden");
    errorBox.classList.add("hidden");
    copyButton.classList.add("hidden");
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
      const elapsed = Math.round(performance.now() - startedAt);
      lastAnswer = chat.answer;
      answer.textContent = chat.answer;
      const metadata = chat.metadata || {};
      const provider = metadata.provider || "unknown";
      const model = metadata.used_model_id || "default";
      const serverMs = metadata.timing?.total_ms;
      responseMeta.textContent = `${providerLabel(provider)} · ${model} · server ${serverMs ?? "-"}ms · round trip ${elapsed}ms · ${metadata.request_id || "-"}`;
      renderSources(chat.source);
      result.classList.remove("hidden");
      copyButton.classList.remove("hidden");
      requestStatus.textContent = "완료";
      requestStatus.className = "rounded-full border border-mint-300/15 bg-mint-400/5 px-2.5 py-1 text-[10px] text-mint-300";
    } catch (error) {
      const message = error instanceof Error ? error.message : "알 수 없는 요청 오류";
      errorBox.textContent = message;
      errorBox.classList.remove("hidden");
      responseMeta.textContent = `요청 실패 · ${Math.round(performance.now() - startedAt)}ms`;
      requestStatus.textContent = "실패";
      requestStatus.className = "rounded-full border border-danger-300/15 bg-danger-300/5 px-2.5 py-1 text-[10px] text-danger-300";
    } finally {
      submitButton.disabled = false;
      submitButton.textContent = "모델 실행";
    }
  };

  modelSelect.addEventListener("change", updateModelDetail);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void runModel();
  });
  promptInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  copyButton.addEventListener("click", () => {
    if (lastAnswer) void navigator.clipboard.writeText(lastAnswer);
  });
  void loadModels();
}
