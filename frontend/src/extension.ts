import * as path from 'node:path';
import * as vscode from 'vscode';

type Source = {
  document_id: string;
  chunk_id: string;
  path?: string;
  language?: string;
  text: string;
  score: number;
};

type ChatResponse = {
  project_id: string;
  session_id?: string;
  answer: string;
  sources: Source[];
  metadata: Record<string, unknown>;
};

type HistoryMessage = { role: 'user' | 'assistant'; content: string };

export function activate(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.commands.registerCommand('hancomAi.openChat', () => {
      AssistantPanel.createOrShow(context.extensionUri);
    }),
    vscode.commands.registerCommand('hancomAi.indexCurrentFile', async () => {
      const panel = AssistantPanel.createOrShow(context.extensionUri);
      await panel.indexActiveEditor();
    }),
    vscode.commands.registerCommand('hancomAi.checkBackend', async () => {
      try {
        const health = await apiRequest<Record<string, unknown>>('/v1/health');
        vscode.window.showInformationMessage(
          `Hancom AI 백엔드 연결 성공: ${String(health.status ?? 'ok')}`,
        );
      } catch (error) {
        vscode.window.showErrorMessage(errorMessage(error));
      }
    }),
  );
}

export function deactivate(): void {}

class AssistantPanel {
  public static currentPanel: AssistantPanel | undefined;
  private readonly panel: vscode.WebviewPanel;
  private readonly disposables: vscode.Disposable[] = [];
  private readonly history: HistoryMessage[] = [];

  public static createOrShow(extensionUri: vscode.Uri): AssistantPanel {
    const column = vscode.window.activeTextEditor?.viewColumn ?? vscode.ViewColumn.One;
    if (AssistantPanel.currentPanel) {
      AssistantPanel.currentPanel.panel.reveal(column);
      return AssistantPanel.currentPanel;
    }
    const panel = vscode.window.createWebviewPanel(
      'hancomAiAssistant',
      'Hancom AI Assistant',
      column,
      { enableScripts: true, retainContextWhenHidden: true },
    );
    AssistantPanel.currentPanel = new AssistantPanel(panel, extensionUri);
    return AssistantPanel.currentPanel;
  }

  private constructor(panel: vscode.WebviewPanel, extensionUri: vscode.Uri) {
    this.panel = panel;
    this.panel.webview.html = this.html(this.panel.webview, extensionUri);
    this.panel.onDidDispose(() => this.dispose(), null, this.disposables);
    this.panel.webview.onDidReceiveMessage(
      async (message: { type?: string; value?: string }) => {
        switch (message.type) {
          case 'chat':
            await this.chat(message.value ?? '');
            break;
          case 'index':
            await this.indexActiveEditor();
            break;
          case 'health':
            await this.checkHealth();
            break;
        }
      },
      null,
      this.disposables,
    );
    void this.checkHealth();
  }

  public async indexActiveEditor(): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      await this.post({ type: 'error', message: '인덱싱할 활성 편집기가 없습니다.' });
      return;
    }
    const document = editor.document;
    if (document.isUntitled) {
      await this.post({ type: 'error', message: '파일을 저장한 후 인덱싱해 주세요.' });
      return;
    }
    await this.post({ type: 'busy', value: true, message: '현재 파일을 인덱싱하고 있습니다…' });
    try {
      const relativePath = vscode.workspace.asRelativePath(document.uri, false);
      const result = await apiRequest<{
        documents_received: number;
        chunks_stored: number;
        embedding_provider: string;
      }>('/v1/documents/ingest', {
        method: 'POST',
        body: JSON.stringify({
          project_id: projectId(),
          documents: [
            {
              document_id: document.uri.toString(),
              text: document.getText(),
              path: relativePath,
              language: document.languageId,
              metadata: { file_name: path.basename(document.fileName) },
            },
          ],
        }),
      });
      await this.post({
        type: 'notice',
        message: `${relativePath}: ${result.chunks_stored}개 청크 저장 (${result.embedding_provider})`,
      });
    } catch (error) {
      await this.post({ type: 'error', message: errorMessage(error) });
    } finally {
      await this.post({ type: 'busy', value: false });
    }
  }

  private async chat(message: string): Promise<void> {
    const question = message.trim();
    if (!question) {
      return;
    }
    await this.post({ type: 'busy', value: true, message: '프로젝트를 검색하고 답변을 생성합니다…' });
    try {
      const response = await apiRequest<ChatResponse>('/v1/chat', {
        method: 'POST',
        body: JSON.stringify({
          project_id: projectId(),
          message: question,
          session_id: projectId(),
          top_k: vscode.workspace.getConfiguration('hancomAi').get<number>('topK', 5),
          history: this.history.slice(-10),
        }),
      });
      this.history.push(
        { role: 'user', content: question },
        { role: 'assistant', content: response.answer },
      );
      await this.post({
        type: 'answer',
        answer: response.answer,
        sources: response.sources,
        metadata: response.metadata,
      });
    } catch (error) {
      await this.post({ type: 'error', message: errorMessage(error) });
    } finally {
      await this.post({ type: 'busy', value: false });
    }
  }

  private async checkHealth(): Promise<void> {
    try {
      const response = await apiRequest<{
        status: string;
        configuration: Record<string, unknown>;
        vector_store: Record<string, number>;
      }>('/v1/health');
      await this.post({
        type: 'health',
        ok: response.status === 'ok',
        message: `연결됨 · ${String(response.configuration.ai_provider)} · ${response.vector_store.chunks ?? 0} chunks`,
      });
    } catch {
      await this.post({
        type: 'health',
        ok: false,
        message: '백엔드 연결 안 됨 · 먼저 FastAPI 서버를 실행하세요.',
      });
    }
  }

  private post(message: Record<string, unknown>): Thenable<boolean> {
    return this.panel.webview.postMessage(message);
  }

  private dispose(): void {
    AssistantPanel.currentPanel = undefined;
    while (this.disposables.length) {
      this.disposables.pop()?.dispose();
    }
  }

  private html(webview: vscode.Webview, _extensionUri: vscode.Uri): string {
    const nonce = getNonce();
    return `<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'nonce-${nonce}'; script-src 'nonce-${nonce}';">
  <title>Hancom AI Assistant</title>
  <style nonce="${nonce}">
    :root { color-scheme: light dark; }
    body { margin: 0; padding: 16px; color: var(--vscode-foreground); background: var(--vscode-editor-background); font-family: var(--vscode-font-family); }
    .header { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 14px; }
    h1 { font-size: 16px; margin: 0; }
    #status { font-size: 12px; color: var(--vscode-descriptionForeground); }
    #status.ok { color: var(--vscode-testing-iconPassed); }
    #status.bad { color: var(--vscode-testing-iconFailed); }
    #messages { display: flex; flex-direction: column; gap: 10px; min-height: 180px; margin-bottom: 14px; }
    .message { border: 1px solid var(--vscode-panel-border); border-radius: 7px; padding: 10px; white-space: pre-wrap; line-height: 1.5; }
    .user { background: var(--vscode-textBlockQuote-background); }
    .assistant { background: var(--vscode-editor-inactiveSelectionBackground); }
    .notice { color: var(--vscode-descriptionForeground); font-size: 12px; }
    .error { color: var(--vscode-errorForeground); }
    .sources { margin-top: 10px; border-top: 1px solid var(--vscode-panel-border); padding-top: 8px; }
    .source { margin-top: 6px; font-size: 12px; color: var(--vscode-descriptionForeground); }
    textarea { box-sizing: border-box; width: 100%; min-height: 84px; resize: vertical; padding: 9px; color: var(--vscode-input-foreground); background: var(--vscode-input-background); border: 1px solid var(--vscode-input-border); }
    .actions { display: flex; gap: 8px; margin-top: 8px; }
    button { border: 0; padding: 7px 12px; cursor: pointer; color: var(--vscode-button-foreground); background: var(--vscode-button-background); }
    button.secondary { color: var(--vscode-button-secondaryForeground); background: var(--vscode-button-secondaryBackground); }
    button:disabled { opacity: .55; cursor: default; }
  </style>
</head>
<body>
  <div class="header"><h1>Hancom AI Assistant</h1><span id="status">연결 확인 중…</span></div>
  <div id="messages"><div class="notice">현재 파일을 인덱싱한 뒤 프로젝트 코드에 관해 질문해 보세요.</div></div>
  <textarea id="question" placeholder="예: 이 파일에서 오류 처리는 어디에서 하나요?"></textarea>
  <div class="actions">
    <button id="send">질문 보내기</button>
    <button id="index" class="secondary">현재 파일 인덱싱</button>
    <button id="health" class="secondary">연결 확인</button>
  </div>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const messages = document.getElementById('messages');
    const question = document.getElementById('question');
    const send = document.getElementById('send');
    const index = document.getElementById('index');
    const health = document.getElementById('health');
    const status = document.getElementById('status');

    function append(kind, text, sources) {
      const box = document.createElement('div');
      box.className = 'message ' + kind;
      const content = document.createElement('div');
      content.textContent = text;
      box.appendChild(content);
      if (Array.isArray(sources) && sources.length) {
        const list = document.createElement('div');
        list.className = 'sources';
        const title = document.createElement('strong');
        title.textContent = '근거 문서';
        list.appendChild(title);
        sources.forEach((source, i) => {
          const item = document.createElement('div');
          item.className = 'source';
          item.textContent = '[' + (i + 1) + '] ' + (source.path || source.document_id) + ' · score ' + Number(source.score).toFixed(3);
          list.appendChild(item);
        });
        box.appendChild(list);
      }
      messages.appendChild(box);
      box.scrollIntoView({ behavior: 'smooth' });
    }

    function submit() {
      const value = question.value.trim();
      if (!value) return;
      append('user', value);
      question.value = '';
      vscode.postMessage({ type: 'chat', value });
    }

    send.addEventListener('click', submit);
    index.addEventListener('click', () => vscode.postMessage({ type: 'index' }));
    health.addEventListener('click', () => vscode.postMessage({ type: 'health' }));
    question.addEventListener('keydown', event => {
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) submit();
    });
    window.addEventListener('message', event => {
      const message = event.data;
      if (message.type === 'busy') {
        send.disabled = index.disabled = Boolean(message.value);
        if (message.message) status.textContent = message.message;
      } else if (message.type === 'health') {
        status.textContent = message.message;
        status.className = message.ok ? 'ok' : 'bad';
      } else if (message.type === 'answer') {
        append('assistant', message.answer, message.sources);
      } else if (message.type === 'notice') {
        append('notice', message.message);
      } else if (message.type === 'error') {
        append('error', message.message);
      }
    });
  </script>
</body>
</html>`;
  }
}

function backendUrl(): string {
  return vscode.workspace
    .getConfiguration('hancomAi')
    .get<string>('backendUrl', 'http://127.0.0.1:8000')
    .replace(/\/$/, '');
}

function projectId(): string {
  const configured = vscode.workspace.getConfiguration('hancomAi').get<string>('projectId', '').trim();
  return configured || vscode.workspace.workspaceFolders?.[0]?.name || 'default';
}

async function apiRequest<T>(endpoint: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 65_000);
  try {
    const response = await fetch(`${backendUrl()}${endpoint}`, {
      ...init,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) },
    });
    const data = (await response.json()) as Record<string, unknown>;
    if (!response.ok) {
      throw new Error(String(data.detail ?? `백엔드 오류: HTTP ${response.status}`));
    }
    return data as T;
  } finally {
    clearTimeout(timeout);
  }
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.name === 'AbortError') {
    return '백엔드 요청 시간이 초과되었습니다.';
  }
  return error instanceof Error ? error.message : String(error);
}

function getNonce(): string {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  let value = '';
  for (let index = 0; index < 32; index += 1) {
    value += alphabet.charAt(Math.floor(Math.random() * alphabet.length));
  }
  return value;
}
