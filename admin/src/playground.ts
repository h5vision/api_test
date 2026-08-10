import DOMPurify from "dompurify";
import { marked } from "marked";

type ModelInfo = {
  model_id: string;
  model_name?: string;
  display_name: string;
  provider: "backendai" | "nvidia" | "groq" | "local" | string;
  location: "internal" | "cloud" | "local";
  deployment_type?: "cloud" | "local" | "remote_server";
  endpoint?: string | null;
  available: boolean;
  is_default: boolean;
  streaming: boolean;
};

type ModelListResponse = { default_model_id?: string; models: ModelInfo[] };
type ModelScope = "sllm" | "cloud" | "all";

type IndexedProject = {
  project_id: string;
  project_name: string;
  git_short_sha?: string | null;
  git_branch?: string | null;
  active_snapshot_id?: string | null;
  index_status: "not_indexed" | "queued" | "indexing" | "ready" | "failed";
  indexed_at?: string | null;
};

type IndexedProjectListResponse = { projects: IndexedProject[]; total: number };

type ChatSource = { file: string; chunk: string; score?: number | null };

type ChatResponse = {
  answer: string;
  source: ChatSource[];
  metadata: {
    request_id?: string;
    used_model_id?: string;
    provider?: string;
    timing?: { total_ms?: number };
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

type HistoryMessage = { role: "user" | "assistant"; content: string };

type AttachedFile = {
  id: string;
  name: string;
  size: number;
  mimeType: string;
  kind: "text" | "image";
  content: string;
  previewUrl?: string;
  path?: string;
  languageId?: string;
  languageName?: string;
  languageSource?: string;
  languageConfidence?: number;
  languageEvidence?: string[];
  languageCandidates?: Array<{ language_id: string; score: number; confidence: number; sources: string[] }>;
};

type LanguageDefinition = {
  id: string;
  aliases: string[];
  extensions: string[];
  filenames: string[];
  filename_patterns: string[];
  first_lines: string[];
};

type LanguageRegistryResponse = {
  registry_revision: string;
  source_version?: string | null;
  languages: LanguageDefinition[];
};

type LanguageDetectionResponse = {
  language_id: string;
  display_name: string;
  source: string;
  confidence: number;
  evidence_sources: string[];
  candidates: Array<{ language_id: string; score: number; confidence: number; sources: string[] }>;
};

type ChatSessionMessage = {
  request_id: string;
  received_at: string;
  completed_at?: string | null;
  question?: string | null;
  answer?: string | null;
  status: string;
  status_code?: number | null;
  used_model_id?: string | null;
  requested_model_id?: string | null;
  provider?: string | null;
  source_count?: number | null;
  duration_ms?: number | null;
  error?: string | null;
};

type ChatSession = {
  session_id: string;
  title: string;
  project_id: string;
  last_message_at: string;
  message_count: number;
  status: string;
  model_id?: string | null;
  provider?: string | null;
  messages: ChatSessionMessage[];
};

type ChatSessionUser = {
  user_key: string;
  display_name: string;
  client_id?: string | null;
  last_message_at: string;
  sessions: ChatSession[];
};

type ChatSessionListResponse = {
  users: ChatSessionUser[];
  total_users: number;
  total_sessions: number;
};

type StreamEnvelope = {
  message?: string;
  text?: string;
  answer?: string;
  source?: ChatSource[];
  metadata?: ChatResponse["metadata"];
  status_code?: number;
  retryable?: boolean;
};

marked.setOptions({ gfm: true, breaks: true });

const icons = {
  attach: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m20.5 11.5-8.2 8.2a5 5 0 0 1-7.1-7.1l9-9a3.5 3.5 0 0 1 5 5l-9.1 9.1a2 2 0 0 1-2.8-2.8l8.2-8.2"/></svg>`,
  send: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 12 6-6 6 6M12 6v12"/></svg>`,
  stop: `<svg viewBox="0 0 24 24" aria-hidden="true" fill="currentColor" stroke="none"><rect x="7" y="7" width="10" height="10" rx="2"/></svg>`,
  newChat: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>`,
  refresh: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6"/></svg>`,
  search: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m16.5 16.5 4 4"/></svg>`,
  chevron: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>`,
  image: `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="9" cy="10" r="2"/><path d="m4 17 5-5 4 4 2-2 5 5"/></svg>`,
  file: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h8l4 4v14H6zM14 3v5h5"/></svg>`,
};

export function playgroundMarkup(): string {
  return `
    <div class="playground-shell enter">
      <aside id="playground-history-rail" class="playground-history-rail" aria-label="채팅 기록">
        <div class="playground-history-head">
          <button id="playground-new-chat" type="button" class="playground-new-chat">${icons.newChat}<span>새 대화</span></button>
          <button id="playground-history-refresh" type="button" class="playground-icon-button" title="대화 기록 새로고침" aria-label="대화 기록 새로고침">${icons.refresh}</button>
        </div>
        <label class="playground-user-field">
          <span>현재 사용자</span>
          <input id="playground-user-name" type="text" maxlength="60" autocomplete="off" value="관리자" aria-label="현재 사용자 이름" />
        </label>
        <label class="playground-history-search">
          ${icons.search}
          <input id="playground-history-search" type="search" placeholder="사용자 또는 대화 검색" aria-label="사용자 또는 대화 검색" />
        </label>
        <div class="playground-history-meta"><span id="playground-history-status">기록 불러오는 중</span><span id="playground-history-count">--</span></div>
        <div id="playground-history-list" class="playground-history-list"></div>
        <details class="playground-project-drawer" open>
          <summary><span>프로젝트 범위</span><span id="playground-project-count">--</span></summary>
          <div class="playground-project-toolbar"><span id="playground-project-status">목록 불러오는 중</span><button id="playground-project-refresh" type="button">새로고침</button></div>
          <div id="playground-project-list" class="playground-project-list" role="listbox" aria-label="인덱싱된 프로젝트"></div>
        </details>
      </aside>

      <section class="playground-conversation" aria-label="Vision AI 채팅">
        <header class="playground-chat-header">
          <button id="playground-history-toggle" type="button" class="playground-icon-button playground-history-toggle" aria-label="채팅 기록 열기" title="채팅 기록">${icons.chevron}</button>
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2">
              <span class="playground-ai-mark">V</span>
              <h1 id="playground-session-title" class="truncate text-sm font-semibold">새 대화</h1>
            </div>
            <p id="playground-chat-scope" class="mt-1 truncate pl-9 font-mono text-[9px] text-white/30">프로젝트를 선택하세요</p>
          </div>
          <div class="playground-chat-controls">
            <label class="sr-only" for="playground-reasoning">추론 모드</label>
            <select id="playground-reasoning" class="playground-control" title="추론 모드">
              <option value="auto" selected>Auto</option><option value="fast">Fast</option><option value="balanced">Balanced</option><option value="deep">Deep</option>
            </select>
            <label class="sr-only" for="playground-model-scope">모델 위치</label>
            <select id="playground-model-scope" class="playground-control playground-model-scope" title="모델 위치 필터">
              <option value="sllm" selected>sLLM</option><option value="cloud">Cloud</option><option value="all">전체</option>
            </select>
            <label class="sr-only" for="playground-model">AI 모델</label>
            <select id="playground-model" class="playground-control playground-model-select" disabled><option>모델 불러오는 중</option></select>
            <button id="playground-model-refresh" type="button" class="playground-icon-button playground-model-refresh" title="모델 다시 감지" aria-label="모델 다시 감지">${icons.refresh}</button>
          </div>
        </header>
        <div id="playground-model-detail" class="playground-model-detail">GET /v1/models 응답을 기다립니다.</div>
        <input id="playground-project" type="hidden" value="" />
        <input id="playground-session" type="hidden" value="" />

        <div id="playground-chat-log" class="playground-chat-log" aria-live="polite">
          <div id="playground-empty" class="playground-empty">
            <span class="playground-empty-mark">V</span>
            <h2>무엇을 함께 살펴볼까요?</h2>
            <p>프로젝트를 선택하면 코드 범위를 전달하고, 선택하지 않으면 일반 질의로 처리합니다.</p>
            <div class="playground-suggestions">
              <button type="button" data-playground-suggestion="이 프로젝트의 전체 실행 구조를 Markdown으로 설명해줘.">프로젝트 실행 구조</button>
              <button type="button" data-playground-suggestion="첨부한 파일의 핵심 흐름과 개선점을 알려줘.">첨부 파일 검토</button>
              <button type="button" data-playground-suggestion="오류의 가능한 원인과 확인 순서를 정리해줘.">오류 원인 분석</button>
            </div>
          </div>
        </div>

        <div class="playground-composer-zone">
          <div id="playground-error" class="playground-error hidden" role="alert"></div>
          <div id="playground-attachments" class="playground-attachments hidden"></div>
          <form id="playground-form" class="playground-composer">
            <input id="playground-file-input" class="hidden" type="file" multiple accept="image/*,.txt,.md,.py,.ts,.tsx,.js,.jsx,.json,.yaml,.yml,.toml,.ini,.cfg,.java,.kt,.go,.rs,.c,.h,.cpp,.hpp,.cs,.html,.css,.scss,.sql,.sh,.ps1,.xml" />
            <label class="sr-only" for="playground-prompt">질문</label>
            <textarea id="playground-prompt" rows="1" placeholder="Vision에게 물어보세요" required></textarea>
            <div class="playground-composer-actions">
              <div class="flex min-w-0 items-center gap-2">
                <button id="playground-attach" type="button" class="playground-round-button" title="파일 또는 이미지 추가" aria-label="파일 또는 이미지 추가">${icons.attach}</button>
                <span id="playground-attachment-summary" class="truncate text-[10px] text-white/28">파일·이미지 추가</span>
              </div>
              <div class="flex shrink-0 items-center gap-2">
                <span id="playground-request-status" class="playground-request-status" data-tone="idle">준비</span>
                <button id="playground-submit" type="submit" class="playground-submit" title="메시지 보내기" aria-label="메시지 보내기">${icons.send}</button>
              </div>
            </div>
          </form>
          <p class="playground-disclaimer">AI가 실수할 수 있습니다. 중요한 정보는 원문과 근거를 확인하세요.</p>
          <details class="playground-request-details"><summary>전송 JSON 확인</summary><pre id="playground-request-preview">{}</pre></details>
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

function safeMarkdown(value: string): string {
  const rendered = marked.parse(value || "", { async: false }) as string;
  return DOMPurify.sanitize(rendered, { USE_PROFILES: { html: true } });
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(2)} MB`;
}

function formatRelativeTime(value: string): string {
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return "";
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (seconds < 60) return "방금";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분 전`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}시간 전`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}일 전`;
  return new Intl.DateTimeFormat("ko-KR", { month: "short", day: "numeric" }).format(new Date(value));
}

function readImage(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("이미지 파일을 읽지 못했습니다."));
    reader.readAsDataURL(file);
  });
}

export function startPlayground(apiBaseUrl: string, adminApiBaseUrl = "/admin-api"): void {
  const form = requiredElement<HTMLFormElement>("playground-form");
  const reasoningSelect = requiredElement<HTMLSelectElement>("playground-reasoning");
  const modelScopeSelect = requiredElement<HTMLSelectElement>("playground-model-scope");
  const modelSelect = requiredElement<HTMLSelectElement>("playground-model");
  const modelRefresh = requiredElement<HTMLButtonElement>("playground-model-refresh");
  const modelDetail = requiredElement<HTMLDivElement>("playground-model-detail");
  const projectInput = requiredElement<HTMLInputElement>("playground-project");
  const projectList = requiredElement<HTMLDivElement>("playground-project-list");
  const projectStatus = requiredElement<HTMLSpanElement>("playground-project-status");
  const projectCount = requiredElement<HTMLSpanElement>("playground-project-count");
  const projectRefresh = requiredElement<HTMLButtonElement>("playground-project-refresh");
  const sessionInput = requiredElement<HTMLInputElement>("playground-session");
  const sessionTitle = requiredElement<HTMLHeadingElement>("playground-session-title");
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
  const historyRefresh = requiredElement<HTMLButtonElement>("playground-history-refresh");
  const historySearch = requiredElement<HTMLInputElement>("playground-history-search");
  const historyList = requiredElement<HTMLDivElement>("playground-history-list");
  const historyStatus = requiredElement<HTMLSpanElement>("playground-history-status");
  const historyCount = requiredElement<HTMLSpanElement>("playground-history-count");
  const userNameInput = requiredElement<HTMLInputElement>("playground-user-name");
  const historyRail = requiredElement<HTMLElement>("playground-history-rail");
  const historyToggle = requiredElement<HTMLButtonElement>("playground-history-toggle");

  let models: ModelInfo[] = [];
  let projects: IndexedProject[] = [];
  let sessionUsers: ChatSessionUser[] = [];
  let expandedUsers = new Set<string>();
  let history: HistoryMessage[] = [];
  let attachments: AttachedFile[] = [];
  let languageRegistry: LanguageRegistryResponse | null = null;
  let languageRegistryLoad: Promise<void> | null = null;
  let requestInFlight = false;
  let activeController: AbortController | null = null;
  const maxContextBytes = 4_900_000;
  const maxAttachmentFiles = 20;
  const createSessionId = () => `playground-${crypto.randomUUID()}`;

  sessionInput.value = createSessionId();
  userNameInput.value = localStorage.getItem("vision-playground-user-name") || "관리자";
  const storedReasoningMode = localStorage.getItem("vision-playground-reasoning-mode");
  if (storedReasoningMode && ["auto", "fast", "balanced", "deep"].includes(storedReasoningMode)) reasoningSelect.value = storedReasoningMode;
  const storedModelScope = localStorage.getItem("vision-playground-model-scope");
  if (storedModelScope && ["sllm", "cloud", "all"].includes(storedModelScope)) modelScopeSelect.value = storedModelScope;

  const adminHeaders = (requestId?: string): Record<string, string> => ({
    "X-Client-Type": "admin-playground",
    "X-Client-ID": `admin-playground:${encodeURIComponent(userNameInput.value.trim() || "관리자")}`,
    "X-Client-Name": "Vision Admin Playground",
    "X-Client-User": encodeURIComponent(userNameInput.value.trim() || "관리자"),
    ...(requestId ? { "X-Request-ID": requestId } : {}),
  });

  const providerLabel = (provider: string): string => ({ backendai: "OLLAMA", nvidia: "NVIDIA", groq: "GROQ", local: "LOCAL" })[provider] || provider.toUpperCase();
  const deploymentLabel = (model: ModelInfo): string => ({ cloud: "Cloud", local: "Local", remote_server: "특정 서버" })[model.deployment_type || (model.location === "internal" ? "remote_server" : model.location)];
  const isSllmModel = (model: ModelInfo): boolean => (
    model.provider === "backendai"
    || model.provider === "local"
    || model.deployment_type === "local"
    || model.deployment_type === "remote_server"
    || (model.location === "internal" && model.provider !== "nvidia" && model.provider !== "groq")
  );
  const modelName = (model: ModelInfo): string => model.model_name || model.display_name;

  const setRequestStatus = (label: string, tone: "idle" | "busy" | "success" | "error") => {
    requestStatus.textContent = label;
    requestStatus.dataset.tone = tone;
  };

  const setError = (message = "") => {
    errorBox.textContent = message;
    errorBox.classList.toggle("hidden", !message);
  };

  const loadLanguages = (): Promise<void> => {
    if (languageRegistryLoad) return languageRegistryLoad;
    languageRegistryLoad = fetch(`${apiBaseUrl}/v1/languages`, {
      headers: { Accept: "application/json", ...adminHeaders() },
      cache: "force-cache",
    }).then(async (response) => {
      if (!response.ok) throw new Error(`Language registry HTTP ${response.status}`);
      languageRegistry = (await response.json()) as LanguageRegistryResponse;
    }).catch((error) => {
      languageRegistry = null;
      console.warn("VS Code language registry unavailable", error);
    });
    return languageRegistryLoad;
  };

  const globRegex = (pattern: string): RegExp => {
    let source = "";
    for (let index = 0; index < pattern.length; index += 1) {
      const character = pattern[index];
      if (character === "*") {
        if (pattern[index + 1] === "*") { source += ".*"; index += 1; }
        else source += "[^/]*";
      } else if (character === "?") source += "[^/]";
      else source += character.replace(/[\\^$+?.()|{}\[\]]/g, "\\$&");
    }
    return new RegExp(`^${source}$`, "i");
  };

  const languageDisplayName = (language: LanguageDefinition): string => language.aliases[0] || language.id;

  const detectFileLanguage = (path: string, content: string): Pick<AttachedFile, "languageId" | "languageName" | "languageSource" | "languageConfidence"> => {
    const normalizedPath = path.replace(/\\/g, "/");
    const fileName = normalizedPath.split("/").pop() || normalizedPath;
    const languages = languageRegistry?.languages || [];
    const exact = languages.find((language) => language.filenames.some((name) => name.toLowerCase() === fileName.toLowerCase()));
    if (exact) return { languageId: exact.id, languageName: languageDisplayName(exact), languageSource: "filename", languageConfidence: 0.99 };
    for (const language of languages) {
      if (language.filename_patterns.some((pattern) => {
        try { return globRegex(pattern).test(normalizedPath) || globRegex(pattern).test(fileName); }
        catch { return false; }
      })) return { languageId: language.id, languageName: languageDisplayName(language), languageSource: "filename_pattern", languageConfidence: 0.96 };
    }
    const extensionMatch = languages.flatMap((language) => language.extensions.map((extension) => ({ language, extension })))
      .sort((left, right) => right.extension.length - left.extension.length)
      .find(({ extension }) => fileName.toLowerCase().endsWith(extension.toLowerCase()));
    if (extensionMatch) return { languageId: extensionMatch.language.id, languageName: languageDisplayName(extensionMatch.language), languageSource: "extension", languageConfidence: 0.94 };
    const firstLine = content.split(/\r?\n/, 1)[0] || "";
    for (const language of languages) {
      if (language.first_lines.some((expression) => {
        try { return new RegExp(expression).test(firstLine); }
        catch { return false; }
      })) return { languageId: language.id, languageName: languageDisplayName(language), languageSource: "first_line", languageConfidence: 0.9 };
    }
    return { languageId: "plaintext", languageName: "Plain Text", languageSource: "fallback", languageConfidence: 0.2 };
  };

  const refineFileLanguage = async (
    path: string,
    content: string,
    initial: ReturnType<typeof detectFileLanguage>,
  ): Promise<Pick<AttachedFile, "languageId" | "languageName" | "languageSource" | "languageConfidence" | "languageEvidence" | "languageCandidates">> => {
    if (initial.languageSource !== "fallback" && (initial.languageConfidence || 0) >= 0.9) return initial;
    try {
      const response = await fetch(`${apiBaseUrl}/v1/languages/detect`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json", ...adminHeaders() },
        body: JSON.stringify({
          file_name: path.split(/[\\/]/).pop() || path,
          path,
          content: content.slice(0, 100_000),
          workspace_languages: attachments.map((file) => file.languageId).filter(Boolean),
        }),
      });
      if (!response.ok) return initial;
      const detected = (await response.json()) as LanguageDetectionResponse;
      return {
        languageId: detected.language_id,
        languageName: detected.display_name,
        languageSource: detected.source,
        languageConfidence: detected.confidence,
        languageEvidence: detected.evidence_sources,
        languageCandidates: detected.candidates,
      };
    } catch {
      return initial;
    }
  };

  const normalizeLanguageId = (value: string): string => {
    const candidate = value.trim().toLowerCase();
    const commonAliases: Record<string, string> = {
      js: "javascript", jsx: "javascriptreact", ts: "typescript", tsx: "typescriptreact",
      py: "python", sh: "shellscript", bash: "shellscript", zsh: "shellscript",
      ps1: "powershell", cs: "csharp", "c#": "csharp", fs: "fsharp",
      "c++": "cpp", rb: "ruby", rs: "rust",
    };
    if (commonAliases[candidate]) return commonAliases[candidate];
    const match = languageRegistry?.languages.find((language) => language.id.toLowerCase() === candidate || language.aliases.some((alias) => alias.toLowerCase() === candidate));
    return match?.id || value;
  };

  const scrollChatToBottom = () => requestAnimationFrame(() => { chatLog.scrollTop = chatLog.scrollHeight; });

  const enhanceMarkdown = (root: HTMLElement) => {
    root.querySelectorAll<HTMLAnchorElement>("a[href]").forEach((link) => {
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    });
    root.querySelectorAll<HTMLElement>("pre").forEach((pre) => {
      if (pre.querySelector("button")) return;
      const code = pre.querySelector("code");
      const declaredLanguage = Array.from(code?.classList || []).find((name) => name.startsWith("language-"))?.slice("language-".length);
      if (declaredLanguage && !pre.querySelector(".playground-code-language")) {
        const badge = document.createElement("span");
        badge.className = "playground-code-language";
        badge.textContent = normalizeLanguageId(declaredLanguage);
        pre.append(badge);
      }
      const button = document.createElement("button");
      button.type = "button";
      button.className = "playground-code-copy";
      button.textContent = "복사";
      button.addEventListener("click", () => {
        void navigator.clipboard.writeText(code?.textContent || pre.textContent || "");
        button.textContent = "복사됨";
        window.setTimeout(() => { button.textContent = "복사"; }, 1200);
      });
      pre.append(button);
    });
  };

  const renderMarkdownInto = (element: HTMLElement, value: string) => {
    element.innerHTML = safeMarkdown(value);
    enhanceMarkdown(element);
  };

  const updateModelDetail = () => {
    const selected = models.find((model) => model.model_id === modelSelect.value);
    if (!selected) {
      modelDetail.textContent = "선택 가능한 모델이 없습니다.";
      modelDetail.dataset.online = "false";
      return;
    }
    modelDetail.textContent = `${isSllmModel(selected) ? "sLLM" : "Cloud"} · ${modelName(selected)} · ${providerLabel(selected.provider)} · ${deploymentLabel(selected)}${selected.endpoint ? ` · ${selected.endpoint}` : ""} · ${selected.available ? "사용 가능" : "응답 불가"}${selected.streaming ? " · Streaming" : ""}${selected.is_default ? " · 기본 모델" : ""}`;
    modelDetail.dataset.online = String(selected.available);
  };

  const selectableModels = (): ModelInfo[] => {
    const explicitBackendModels = new Set(
      models
        .filter((model) => model.model_id.startsWith("backendai:") && model.model_name)
        .map((model) => model.model_name as string),
    );
    return models.filter((model) => !(
      model.model_id === "backendai-default"
      && Boolean(model.model_name)
      && explicitBackendModels.has(model.model_name as string)
    ));
  };

  const renderModelOptions = (preferredModelId?: string | null) => {
    const scope = modelScopeSelect.value as ModelScope;
    const candidates = selectableModels()
      .filter((model) => scope === "all" || (scope === "sllm" ? isSllmModel(model) : !isSllmModel(model)))
      .sort((left, right) => Number(right.available) - Number(left.available)
        || Number(right.is_default) - Number(left.is_default)
        || modelName(left).localeCompare(modelName(right), "ko"));

    modelSelect.replaceChildren();
    const appendGroup = (label: string, entries: ModelInfo[]) => {
      if (!entries.length) return;
      const group = document.createElement("optgroup");
      group.label = `${label} (${entries.length})`;
      for (const model of entries) {
        const endpoint = model.endpoint ? ` · ${model.endpoint}` : "";
        const option = new Option(`${modelName(model)} · ${providerLabel(model.provider)}${endpoint}${model.available ? "" : " · offline"}`, model.model_id);
        option.disabled = !model.available;
        group.append(option);
      }
      modelSelect.append(group);
    };
    appendGroup("sLLM · Local / Remote", candidates.filter(isSllmModel));
    appendGroup("Cloud", candidates.filter((model) => !isSllmModel(model)));

    const available = candidates.filter((model) => model.available);
    const stored = localStorage.getItem("vision-playground-model-id");
    const preferred = available.find((model) => model.model_id === preferredModelId)
      || available.find((model) => model.model_id === stored)
      || available.find((model) => model.is_default)
      || available[0];
    if (!preferred) {
      modelSelect.replaceChildren(new Option(scope === "sllm" ? "감지된 sLLM 없음" : "사용 가능한 모델 없음", ""));
      modelSelect.disabled = true;
      updateModelDetail();
      return;
    }
    modelSelect.value = preferred.model_id;
    modelSelect.disabled = requestInFlight;
    localStorage.setItem("vision-playground-model-id", preferred.model_id);
    updateModelDetail();
  };

  const loadModels = async () => {
    modelSelect.disabled = true;
    modelRefresh.disabled = true;
    modelDetail.textContent = "AI Server에서 모델 목록을 다시 감지하는 중입니다.";
    try {
      const response = await fetch(`${apiBaseUrl}/v1/models`, { headers: { Accept: "application/json", ...adminHeaders() }, cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = (await response.json()) as ModelListResponse;
      models = data.models;
      if (!models.some(isSllmModel) && modelScopeSelect.value === "sllm") {
        modelScopeSelect.value = "all";
        localStorage.setItem("vision-playground-model-scope", "all");
      }
      renderModelOptions(data.default_model_id);
    } catch (error) {
      modelSelect.replaceChildren(new Option("모델 조회 실패", ""));
      modelDetail.textContent = error instanceof Error ? error.message : "모델 목록 조회 실패";
      modelDetail.dataset.online = "false";
    } finally {
      modelRefresh.disabled = requestInFlight;
    }
  };

  const renderProjects = () => {
    projectList.replaceChildren();
    const ready = projects.filter((project) => project.index_status === "ready");
    projectCount.textContent = `${ready.length}/${projects.length}`;
    projectStatus.textContent = projects.length ? `${projects.length}개 프로젝트` : "등록 프로젝트 없음";
    const general = document.createElement("button");
    general.type = "button";
    general.dataset.projectId = "__unscoped__";
    general.className = "playground-project-item";
    general.dataset.selected = String(!projectInput.value);
    general.innerHTML = `<span><strong>일반 대화</strong><small>프로젝트 검색 없이 AI와 대화</small></span><em>chat</em>`;
    projectList.append(general);
    for (const project of projects) {
      const selectable = project.index_status === "ready";
      const item = document.createElement("button");
      item.type = "button";
      item.disabled = !selectable;
      item.dataset.projectId = project.project_id;
      item.className = "playground-project-item";
      item.dataset.selected = String(project.project_id === projectInput.value);
      const version = project.git_short_sha ? `${project.git_branch || "git"}@${project.git_short_sha}` : project.active_snapshot_id || "snapshot 없음";
      item.innerHTML = `<span><strong></strong><small></small></span><em></em>`;
      item.querySelector("strong")!.textContent = project.project_name;
      item.querySelector("small")!.textContent = version;
      item.querySelector("em")!.textContent = project.index_status;
      projectList.append(item);
    }
  };

  const selectProject = (projectId: string, reset = true) => {
    if (requestInFlight) return;
    if (projectId === "__unscoped__") {
      const changed = Boolean(projectInput.value);
      projectInput.value = "";
      chatScope.textContent = "일반 대화 · 프로젝트 범위 없음";
      localStorage.setItem("vision-playground-project-id", "__unscoped__");
      if (changed && reset) resetConversation();
      renderProjects();
      return;
    }
    const project = projects.find((item) => item.project_id === projectId);
    if (!project || project.index_status !== "ready") return;
    const changed = projectInput.value !== project.project_id;
    projectInput.value = project.project_id;
    chatScope.textContent = `${project.project_name} · ${project.project_id}`;
    localStorage.setItem("vision-playground-project-id", project.project_id);
    if (changed && reset) resetConversation();
    renderProjects();
  };

  const loadProjects = async () => {
    projectRefresh.disabled = true;
    try {
      const response = await fetch(`${apiBaseUrl}/v1/IngestResponse`, { headers: { Accept: "application/json", ...adminHeaders() }, cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      projects = ((await response.json()) as IndexedProjectListResponse).projects;
      const stored = localStorage.getItem("vision-playground-project-id");
      const preferred = projects.find((project) => project.project_id === stored && project.index_status === "ready") || projects.find((project) => project.index_status === "ready");
      if (stored === "__unscoped__" && !projectInput.value) {
        chatScope.textContent = "일반 대화 · 프로젝트 범위 없음";
      } else if (preferred && !projectInput.value) {
        projectInput.value = preferred.project_id;
        chatScope.textContent = `${preferred.project_name} · ${preferred.project_id}`;
      }
      renderProjects();
    } catch (error) {
      projects = [];
      projectStatus.textContent = error instanceof Error ? error.message : "조회 실패";
      renderProjects();
    } finally {
      projectRefresh.disabled = false;
    }
  };

  const renderSessionUsers = () => {
    historyList.replaceChildren();
    const query = historySearch.value.trim().toLocaleLowerCase("ko-KR");
    const filtered = sessionUsers.map((user) => ({
      ...user,
      sessions: user.sessions.filter((session) => !query || `${user.display_name} ${session.title} ${session.project_id} ${session.session_id}`.toLocaleLowerCase("ko-KR").includes(query)),
    })).filter((user) => user.sessions.length > 0 || (!query && user.sessions.length === 0));

    for (const user of filtered) {
      const group = document.createElement("section");
      group.className = "playground-user-group";
      const open = expandedUsers.has(user.user_key) || query.length > 0;
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "playground-user-toggle";
      toggle.dataset.userKey = user.user_key;
      toggle.setAttribute("aria-expanded", String(open));
      const initial = Array.from(user.display_name.trim())[0] || "?";
      toggle.innerHTML = `<span class="playground-user-avatar"></span><span class="min-w-0 flex-1"><strong></strong><small></small></span>${icons.chevron}`;
      toggle.querySelector(".playground-user-avatar")!.textContent = initial;
      toggle.querySelector("strong")!.textContent = user.display_name;
      toggle.querySelector("small")!.textContent = `${user.sessions.length}개 대화 · ${formatRelativeTime(user.last_message_at)}`;
      group.append(toggle);
      if (open) {
        const sessions = document.createElement("div");
        sessions.className = "playground-session-list";
        for (const session of user.sessions) {
          const button = document.createElement("button");
          button.type = "button";
          button.dataset.sessionId = session.session_id;
          button.dataset.userKey = user.user_key;
          button.className = "playground-session-item";
          button.dataset.active = String(session.session_id === sessionInput.value);
          button.innerHTML = `<strong></strong><span><small></small><em></em></span>`;
          button.querySelector("strong")!.textContent = session.title;
          button.querySelector("small")!.textContent = session.project_id === "__unscoped__" ? "일반 질의" : session.project_id;
          button.querySelector("em")!.textContent = formatRelativeTime(session.last_message_at);
          sessions.append(button);
        }
        group.append(sessions);
      }
      historyList.append(group);
    }
    if (!filtered.length) historyList.innerHTML = `<p class="playground-history-empty">일치하는 채팅 기록이 없습니다.</p>`;
  };

  const loadSessions = async () => {
    historyRefresh.disabled = true;
    historyStatus.textContent = "대화 기록 불러오는 중";
    try {
      const response = await fetch(`${adminApiBaseUrl}/chat-sessions?limit=500`, { headers: { Accept: "application/json" }, cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = (await response.json()) as ChatSessionListResponse;
      sessionUsers = data.users;
      historyStatus.textContent = `${data.total_users}명`;
      historyCount.textContent = `${data.total_sessions} chats`;
      renderSessionUsers();
    } catch (error) {
      sessionUsers = [];
      historyStatus.textContent = error instanceof Error ? error.message : "기록 조회 실패";
      historyCount.textContent = "error";
      renderSessionUsers();
    } finally {
      historyRefresh.disabled = false;
    }
  };

  const renderAttachmentChips = () => {
    attachmentsContainer.replaceChildren();
    attachmentsContainer.classList.toggle("hidden", attachments.length === 0);
    const encodedBytes = new TextEncoder().encode(JSON.stringify(buildAttachmentContext())).byteLength;
    attachmentSummary.textContent = attachments.length ? `${attachments.length}개 · context ${formatBytes(encodedBytes)}` : "파일·이미지 추가";
    for (const file of attachments) {
      const chip = document.createElement("article");
      chip.className = "playground-attachment-chip";
      if (file.kind === "image" && file.previewUrl) {
        const image = document.createElement("img");
        image.src = file.previewUrl;
        image.alt = "";
        chip.append(image);
      } else {
        const icon = document.createElement("span");
        icon.className = "playground-attachment-icon";
        icon.innerHTML = icons.file;
        chip.append(icon);
      }
      const label = document.createElement("span");
      label.innerHTML = `<strong></strong><small></small>`;
      label.querySelector("strong")!.textContent = file.name;
      const confidence = file.kind === "text" && file.languageConfidence != null ? ` · ${Math.round(file.languageConfidence * 100)}%` : "";
      label.querySelector("small")!.textContent = `${file.kind === "image" ? "이미지" : (file.languageName || file.languageId || "문서")}${confidence} · ${formatBytes(file.size)}`;
      if (file.languageCandidates?.length) {
        chip.title = file.languageCandidates.slice(0, 4).map((candidate) => `${candidate.language_id} ${Math.round(candidate.confidence * 100)}%`).join(" · ");
      }
      const remove = document.createElement("button");
      remove.type = "button";
      remove.dataset.attachmentId = file.id;
      remove.setAttribute("aria-label", `${file.name} 제거`);
      remove.textContent = "×";
      chip.append(label, remove);
      attachmentsContainer.append(chip);
    }
  };

  function buildAttachmentContext(): Array<Record<string, unknown>> {
    return attachments.map((file) => ({
      id: `attachment:${file.id}`,
      name: file.name,
      value: file.kind === "image"
        ? { kind: "image", file_name: file.name, mime_type: file.mimeType, size: file.size, data_url: file.content }
        : {
            kind: "text",
            file_name: file.name,
            path: file.path || file.name,
            mime_type: file.mimeType,
            size: file.size,
            content: file.content,
            language_id: file.languageId,
            language_detection: {
              source: file.languageSource,
              confidence: file.languageConfidence,
              registry_revision: languageRegistry?.registry_revision,
              evidence_sources: file.languageEvidence,
              candidates: file.languageCandidates,
            },
          },
    }));
  }

  const appendUserMessage = (message: string, files: AttachedFile[]) => {
    emptyState.classList.add("hidden");
    const row = document.createElement("div");
    row.className = "playground-message playground-message-user";
    const bubble = document.createElement("article");
    const text = document.createElement("p");
    text.textContent = message;
    bubble.append(text);
    if (files.length) {
      const list = document.createElement("div");
      list.className = "playground-message-files";
      for (const file of files) {
        const item = document.createElement("span");
        item.textContent = `${file.kind === "image" ? "이미지" : "파일"} · ${file.name}`;
        list.append(item);
      }
      bubble.append(list);
    }
    row.append(bubble);
    chatLog.append(row);
    scrollChatToBottom();
  };

  const appendAssistantShell = (): { row: HTMLElement; answer: HTMLElement; status: HTMLElement; footer: HTMLElement } => {
    const row = document.createElement("div");
    row.className = "playground-message playground-message-assistant";
    const avatar = document.createElement("span");
    avatar.className = "playground-ai-mark";
    avatar.textContent = "V";
    const body = document.createElement("article");
    const status = document.createElement("div");
    status.className = "playground-stream-status";
    status.innerHTML = `<span></span><strong>전송 중</strong>`;
    const answer = document.createElement("div");
    answer.className = "playground-markdown";
    const footer = document.createElement("div");
    footer.className = "playground-message-footer";
    body.append(status, answer, footer);
    row.append(avatar, body);
    chatLog.append(row);
    scrollChatToBottom();
    return { row, answer, status, footer };
  };

  const createSources = (items: ChatSource[]): HTMLElement | null => {
    if (!items.length) return null;
    const details = document.createElement("details");
    details.className = "playground-sources";
    const summary = document.createElement("summary");
    summary.textContent = `근거 ${items.length}개`;
    const list = document.createElement("div");
    for (const [index, source] of items.entries()) {
      const card = document.createElement("article");
      const header = document.createElement("div");
      const path = document.createElement("strong");
      path.textContent = `[${index + 1}] ${source.file}`;
      const score = document.createElement("span");
      score.textContent = source.score == null ? "score -" : `score ${source.score.toFixed(4)}`;
      const excerpt = document.createElement("p");
      excerpt.textContent = source.chunk;
      header.append(path, score);
      card.append(header, excerpt);
      list.append(card);
    }
    details.append(summary, list);
    return details;
  };

  const finalizeAssistant = (shell: ReturnType<typeof appendAssistantShell>, chat: ChatResponse, elapsed: number) => {
    shell.status.remove();
    renderMarkdownInto(shell.answer, chat.answer || "응답이 비어 있습니다.");
    const sources = createSources(Array.isArray(chat.source) ? chat.source : []);
    if (sources) shell.answer.parentElement?.insertBefore(sources, shell.footer);
    const provider = chat.metadata?.provider || "unknown";
    const model = chat.metadata?.used_model_id || modelSelect.value || "auto";
    shell.footer.innerHTML = `<span></span><button type="button">답변 복사</button>`;
    shell.footer.querySelector("span")!.textContent = `${providerLabel(provider)} · ${model} · ${elapsed}ms`;
    shell.footer.querySelector("button")!.addEventListener("click", (event) => {
      void navigator.clipboard.writeText(chat.answer);
      const button = event.currentTarget as HTMLButtonElement;
      button.textContent = "복사됨";
      window.setTimeout(() => { button.textContent = "답변 복사"; }, 1200);
    });
    scrollChatToBottom();
  };

  const renderStoredSession = async (userKey: string, sessionId: string) => {
    if (requestInFlight) return;
    const user = sessionUsers.find((item) => item.user_key === userKey);
    let session = user?.sessions.find((item) => item.session_id === sessionId);
    if (!user || !session) return;
    if (!session.messages.length) {
      setRequestStatus("기록 불러오는 중", "busy");
      try {
        const query = new URLSearchParams({ client_id: user.client_id || "anonymous", session_id: session.session_id, limit: "200" });
        const response = await fetch(`${adminApiBaseUrl}/chat-session?${query}`, { headers: { Accept: "application/json" }, cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        session = await response.json() as ChatSession;
        const index = user.sessions.findIndex((item) => item.session_id === sessionId);
        if (index >= 0) user.sessions[index] = session;
      } catch (error) {
        setError(error instanceof Error ? `대화 기록 조회 실패: ${error.message}` : "대화 기록 조회 실패");
        setRequestStatus("기록 조회 실패", "error");
        return;
      }
    }
    sessionInput.value = session.session_id;
    sessionTitle.textContent = session.title;
    history = [];
    attachments = [];
    renderAttachmentChips();
    chatLog.replaceChildren();
    for (const message of session.messages) {
      if (message.question) {
        appendUserMessage(message.question, []);
        history.push({ role: "user", content: message.question });
      }
      if (message.answer) {
        const shell = appendAssistantShell();
        finalizeAssistant(shell, { answer: message.answer, source: [], metadata: { used_model_id: message.used_model_id || undefined, provider: message.provider || undefined } }, message.duration_ms || 0);
        history.push({ role: "assistant", content: message.answer });
      } else if (message.error) {
        const shell = appendAssistantShell();
        shell.status.remove();
        shell.answer.textContent = `답변 실패: ${message.error}`;
        shell.answer.classList.add("playground-message-error");
      }
    }
    history = history.slice(-20);
    const project = projects.find((item) => item.project_id === session.project_id && item.index_status === "ready");
    if (project) selectProject(project.project_id, false);
    if (session.model_id && models.some((model) => model.model_id === session.model_id && model.available)) {
      const sessionModel = models.find((model) => model.model_id === session.model_id)!;
      modelScopeSelect.value = isSllmModel(sessionModel) ? "sllm" : "cloud";
      localStorage.setItem("vision-playground-model-scope", modelScopeSelect.value);
      renderModelOptions(session.model_id);
    }
    renderSessionUsers();
    scrollChatToBottom();
    setRequestStatus("기록 열림", "idle");
  };

  function resetConversation(): void {
    if (requestInFlight) return;
    history = [];
    attachments = [];
    sessionInput.value = createSessionId();
    sessionTitle.textContent = "새 대화";
    chatLog.replaceChildren(emptyState);
    emptyState.classList.remove("hidden");
    setError();
    requestPreview.textContent = "{}";
    promptInput.value = "";
    promptInput.style.height = "";
    renderAttachmentChips();
    renderSessionUsers();
    setRequestStatus("준비", "idle");
    promptInput.focus();
  }

  const consumeSse = async (response: Response, shell: ReturnType<typeof appendAssistantShell>): Promise<ChatResponse> => {
    if (!response.body) throw new Error("SSE 응답 본문이 없습니다.");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let answer = "";
    let done: ChatResponse | null = null;

    const handleBlock = (block: string) => {
      let eventName = "message";
      const dataLines: string[] = [];
      for (const line of block.split(/\r?\n/)) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
      }
      if (!dataLines.length) return;
      const data = JSON.parse(dataLines.join("\n")) as StreamEnvelope;
      if (eventName === "meta") {
        setRequestStatus("전송 중", "busy");
        shell.status.querySelector("strong")!.textContent = "전송 중";
      } else if (eventName === "status") {
        setRequestStatus("추론 중", "busy");
        shell.status.querySelector("strong")!.textContent = data.message || "추론 중";
      } else if (eventName === "delta") {
        answer += data.text || "";
        setRequestStatus("답변 중", "busy");
        shell.status.querySelector("strong")!.textContent = "답변 작성 중";
        renderMarkdownInto(shell.answer, answer);
        scrollChatToBottom();
      } else if (eventName === "done") {
        done = data as ChatResponse;
      } else if (eventName === "error") {
        throw new Error(data.message || `Streaming 실패${data.status_code ? ` (HTTP ${data.status_code})` : ""}`);
      }
    };

    while (true) {
      const chunk = await reader.read();
      buffer += decoder.decode(chunk.value || new Uint8Array(), { stream: !chunk.done });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() || "";
      for (const block of blocks) if (block.trim() && !block.trimStart().startsWith(":")) handleBlock(block);
      if (chunk.done) break;
    }
    if (buffer.trim() && !buffer.trimStart().startsWith(":")) handleBlock(buffer);
    return done || { answer, source: [], metadata: {} };
  };

  const runModel = async () => {
    if (requestInFlight) {
      activeController?.abort();
      return;
    }
    const question = promptInput.value.trim();
    if (!question || !modelSelect.value) return;
    const selectedAttachments = [...attachments];
    const context = buildAttachmentContext();
    const contextBytes = new TextEncoder().encode(JSON.stringify(context)).byteLength;
    if (contextBytes > maxContextBytes) {
      setError(`첨부 context는 5MB보다 작아야 합니다. 현재 ${formatBytes(contextBytes)}입니다.`);
      return;
    }
    const clientRequestId = `playground-${crypto.randomUUID()}`;
    const payload = {
      schema_version: "1.0" as const,
      client_request_id: clientRequestId,
      role: "user" as const,
      project_id: projectInput.value.trim() || "__auto__",
      session_id: sessionInput.value.trim(),
      model_id: modelSelect.value,
      content: question,
      reasoning_mode: reasoningSelect.value as "auto" | "fast" | "balanced" | "deep",
      history: history.slice(-20),
      context,
      stream: true,
    };
    requestPreview.textContent = JSON.stringify({ ...payload, context: context.map((item) => ({ ...item, value: { ...(item.value as Record<string, unknown>), content: item.value && "content" in (item.value as Record<string, unknown>) ? `[${(item.value as { content: string }).content.length} chars]` : undefined, data_url: item.value && "data_url" in (item.value as Record<string, unknown>) ? "[image data]" : undefined } })) }, null, 2);

    requestInFlight = true;
    activeController = new AbortController();
    submitButton.innerHTML = icons.stop;
    submitButton.title = "응답 중지";
    submitButton.setAttribute("aria-label", "응답 중지");
    attachButton.disabled = true;
    modelSelect.disabled = true;
    modelScopeSelect.disabled = true;
    modelRefresh.disabled = true;
    reasoningSelect.disabled = true;
    setError();
    setRequestStatus("전송 중", "busy");
    appendUserMessage(question, selectedAttachments);
    const shell = appendAssistantShell();
    promptInput.value = "";
    promptInput.style.height = "";
    if (sessionTitle.textContent === "새 대화") sessionTitle.textContent = question.length > 42 ? `${question.slice(0, 42)}…` : question;
    const startedAt = performance.now();

    try {
      const response = await fetch(`${apiBaseUrl}/v1/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...adminHeaders(clientRequestId) },
        body: JSON.stringify(payload),
        signal: activeController.signal,
      });
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error(errorMessage(data, `HTTP ${response.status}`));
      }
      const contentType = response.headers.get("content-type") || "";
      const chat = contentType.toLowerCase().includes("text/event-stream")
        ? await consumeSse(response, shell)
        : await response.json() as ChatResponse;
      history.push({ role: "user", content: question }, { role: "assistant", content: chat.answer });
      history = history.slice(-20);
      attachments = [];
      renderAttachmentChips();
      finalizeAssistant(shell, chat, Math.round(performance.now() - startedAt));
      setRequestStatus("완료", "success");
      window.setTimeout(() => { void loadSessions(); }, 500);
    } catch (error) {
      const aborted = error instanceof DOMException && error.name === "AbortError";
      shell.status.remove();
      shell.answer.textContent = aborted ? "응답 생성을 중지했습니다." : (error instanceof Error ? error.message : "알 수 없는 요청 오류");
      shell.answer.classList.add("playground-message-error");
      setRequestStatus(aborted ? "중지됨" : "실패", aborted ? "idle" : "error");
      if (!aborted) setError(shell.answer.textContent);
    } finally {
      requestInFlight = false;
      activeController = null;
      submitButton.innerHTML = icons.send;
      submitButton.title = "메시지 보내기";
      submitButton.setAttribute("aria-label", "메시지 보내기");
      attachButton.disabled = false;
      modelScopeSelect.disabled = false;
      modelRefresh.disabled = false;
      reasoningSelect.disabled = false;
      renderModelOptions(modelSelect.value);
      promptInput.focus();
    }
  };

  const addFiles = async (files: File[]) => {
    setError();
    await loadLanguages();
    if (attachments.length + files.length > maxAttachmentFiles) throw new Error(`첨부는 최대 ${maxAttachmentFiles}개까지 가능합니다.`);
    for (const file of files) {
      const image = file.type.startsWith("image/");
      const content = image ? await readImage(file) : await file.text();
      const path = file.webkitRelativePath || file.name;
      const detected = image ? {} : await refineFileLanguage(path, content, detectFileLanguage(path, content));
      attachments.push({ id: crypto.randomUUID(), name: file.name, path, size: file.size, mimeType: file.type || "text/plain", kind: image ? "image" : "text", content, previewUrl: image ? content : undefined, ...detected });
      const contextBytes = new TextEncoder().encode(JSON.stringify(buildAttachmentContext())).byteLength;
      if (contextBytes > maxContextBytes) {
        attachments.pop();
        throw new Error(`${file.name}을 추가하면 Chat context 5MB 제한을 초과합니다.`);
      }
    }
    renderAttachmentChips();
  };

  modelSelect.addEventListener("change", () => { localStorage.setItem("vision-playground-model-id", modelSelect.value); updateModelDetail(); });
  modelScopeSelect.addEventListener("change", () => {
    localStorage.setItem("vision-playground-model-scope", modelScopeSelect.value);
    renderModelOptions();
  });
  modelRefresh.addEventListener("click", () => { void loadModels(); });
  reasoningSelect.addEventListener("change", () => localStorage.setItem("vision-playground-reasoning-mode", reasoningSelect.value));
  userNameInput.addEventListener("change", () => localStorage.setItem("vision-playground-user-name", userNameInput.value.trim() || "관리자"));
  historySearch.addEventListener("input", renderSessionUsers);
  historyRefresh.addEventListener("click", () => { void loadSessions(); });
  historyToggle.addEventListener("click", () => {
    historyRail.dataset.open = String(historyRail.dataset.open !== "true");
  });
  historyList.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target.closest<HTMLButtonElement>("button") : null;
    if (!target) return;
    if (target.classList.contains("playground-user-toggle") && target.dataset.userKey) {
      const key = target.dataset.userKey;
      expandedUsers.has(key) ? expandedUsers.delete(key) : expandedUsers.add(key);
      renderSessionUsers();
    } else if (target.dataset.sessionId && target.dataset.userKey) {
      void renderStoredSession(target.dataset.userKey, target.dataset.sessionId);
      delete historyRail.dataset.open;
    }
  });
  projectList.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target.closest<HTMLButtonElement>("button[data-project-id]") : null;
    if (target?.dataset.projectId) selectProject(target.dataset.projectId);
  });
  projectRefresh.addEventListener("click", () => { void loadProjects(); });
  newChatButton.addEventListener("click", resetConversation);
  attachButton.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", async () => {
    try { await addFiles(Array.from(fileInput.files || [])); }
    catch (error) { setError(error instanceof Error ? error.message : "첨부파일을 읽지 못했습니다."); }
    finally { fileInput.value = ""; }
  });
  attachmentsContainer.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target.closest<HTMLButtonElement>("button[data-attachment-id]") : null;
    if (!target?.dataset.attachmentId) return;
    attachments = attachments.filter((file) => file.id !== target.dataset.attachmentId);
    renderAttachmentChips();
  });
  chatLog.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target.closest<HTMLButtonElement>("button[data-playground-suggestion]") : null;
    if (!target?.dataset.playgroundSuggestion) return;
    promptInput.value = target.dataset.playgroundSuggestion;
    promptInput.dispatchEvent(new Event("input"));
    promptInput.focus();
  });
  form.addEventListener("submit", (event) => { event.preventDefault(); void runModel(); });
  promptInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); form.requestSubmit(); }
  });
  promptInput.addEventListener("input", () => {
    promptInput.style.height = "auto";
    promptInput.style.height = `${Math.min(promptInput.scrollHeight, 200)}px`;
  });
  form.addEventListener("dragover", (event) => { event.preventDefault(); form.dataset.dragging = "true"; });
  form.addEventListener("dragleave", () => { delete form.dataset.dragging; });
  form.addEventListener("drop", (event) => {
    event.preventDefault(); delete form.dataset.dragging;
    void addFiles(Array.from(event.dataTransfer?.files || [])).catch((error) => setError(error instanceof Error ? error.message : "파일을 추가하지 못했습니다."));
  });
  promptInput.addEventListener("paste", (event) => {
    const images = Array.from(event.clipboardData?.files || []).filter((file) => file.type.startsWith("image/"));
    if (images.length) void addFiles(images).catch((error) => setError(error instanceof Error ? error.message : "이미지를 추가하지 못했습니다."));
  });

  void Promise.allSettled([loadModels(), loadProjects(), loadSessions(), loadLanguages()]);
}
