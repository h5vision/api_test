export function vectorManagementMarkup(): string {
  return `
    <article class="panel rounded-2xl p-4 md:col-span-2 xl:col-span-3">
      <div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div class="flex flex-wrap items-center gap-2">
            <h2 class="text-base font-semibold text-white/90">VectorDB · Embedding · Index 운영</h2>
            <span id="vector-runtime-badge" class="rounded-md border border-mint-300/20 bg-mint-400/7 px-2 py-1 font-mono text-[9px] font-bold text-mint-300">VECTOR SEARCH</span>
          </div>
          <p class="mt-1 max-w-3xl text-xs leading-5 text-white/35">연결 대상 등록, Embedding 공간, 프로젝트 검색 Route와 인덱싱 작업을 단계별로 관리합니다. 저장된 VectorTarget과 EmbeddingProfile ID가 Runtime authority입니다.</p>
        </div>
        <div class="grid shrink-0 grid-cols-3 gap-1 text-center text-[9px]">
          <span class="rounded-lg border border-white/7 bg-black/10 px-2 py-1.5 text-white/40">1 · 연결</span>
          <span class="rounded-lg border border-white/7 bg-black/10 px-2 py-1.5 text-white/40">2 · 공간</span>
          <span class="rounded-lg border border-white/7 bg-black/10 px-2 py-1.5 text-white/40">3 · Route</span>
        </div>
      </div>

      <div class="mt-4 grid gap-3 xl:grid-cols-12">
        <section class="rounded-xl border border-white/7 bg-black/10 p-3 xl:col-span-7" aria-labelledby="vector-registry-title">
          <div class="flex items-start justify-between gap-3">
            <div>
              <h3 id="vector-registry-title" class="text-xs font-semibold text-white/75">등록 기본값</h3>
              <p class="mt-1 text-[10px] leading-4 text-white/32">저장하면 VectorTarget과 EmbeddingProfile을 생성 또는 갱신하고 선택 상태를 함께 보존합니다.</p>
            </div>
            <span class="rounded-md bg-mint-400/7 px-2 py-1 font-mono text-[9px] text-mint-300">UPSERT</span>
          </div>

          <form id="vector-settings-form" class="mt-3 space-y-3" autocomplete="off">
            <fieldset class="rounded-lg border border-white/7 p-3">
              <legend class="px-1 text-[10px] font-semibold text-mint-300">1. VectorDB 연결</legend>
              <p class="mb-2 text-[9px] text-white/30">Vision 기본 Qdrant 또는 관리자가 지정한 Qdrant-compatible Target</p>
              <div class="grid gap-2 sm:grid-cols-[1fr_110px]">
                <label><span class="mb-1 block text-[10px] text-white/42">Host 또는 Docker service name</span><input id="vector-host" class="playground-control w-full" required placeholder="qdrant 또는 192.168.0.12" /></label>
                <label><span class="mb-1 block text-[10px] text-white/42">Port</span><input id="vector-port" class="playground-control w-full" type="number" min="1" max="65535" step="1" required placeholder="6333" /></label>
              </div>
            </fieldset>

            <fieldset class="rounded-lg border border-white/7 p-3">
              <legend class="px-1 text-[10px] font-semibold text-mint-300">2. Embedding 공간</legend>
              <p class="mb-2 text-[9px] text-white/30">모델·차원·Provider가 달라지면 기존 Vector Index와 호환되지 않을 수 있습니다.</p>
              <div class="grid gap-2 sm:grid-cols-2">
                <label><span class="mb-1 block text-[10px] text-white/42">실행 위치</span><select id="embedding-deployment" class="playground-control w-full"><option value="api">외부/내부 API</option><option value="local">API Server Local</option></select></label>
                <label><span class="mb-1 block text-[10px] text-white/42">Provider 규격</span><select id="embedding-provider" class="playground-control w-full"><option value="ollama">Ollama-compatible</option><option value="openai">OpenAI-compatible</option><option value="nvidia">NVIDIA-compatible (legacy)</option></select></label>
                <label class="sm:col-span-2"><span class="mb-1 block text-[10px] text-white/42">Embedding Base URL</span><input id="embedding-base-url" class="playground-control w-full" type="url" required placeholder="http://192.168.0.12:11500" /></label>
                <label><span class="mb-1 block text-[10px] text-white/42">실제 모델명</span><input id="embedding-model" class="playground-control w-full" required placeholder="bge-m3:latest" /></label>
                <label><span class="mb-1 block text-[10px] text-white/42">관리 ID</span><input id="embedding-model-id" class="playground-control w-full" required placeholder="bge-m3:latest" /></label>
                <label><span class="mb-1 block text-[10px] text-white/42">Vector Dimension</span><input id="embedding-dimension" class="playground-control w-full" type="number" min="1" max="65536" required placeholder="1024" /></label>
                <label><span class="mb-1 block text-[10px] text-white/42">Batch size</span><input id="embedding-batch-size" class="playground-control w-full" type="number" min="1" max="256" required placeholder="32" /></label>
              </div>
            </fieldset>

            <fieldset class="rounded-lg border border-white/7 p-3">
              <legend class="px-1 text-[10px] font-semibold text-mint-300">3. Index 식별</legend>
              <div class="grid gap-2 sm:grid-cols-2">
                <label><span class="mb-1 block text-[10px] text-white/42">Collection</span><input id="vector-collection" class="playground-control w-full" required placeholder="vision_bge_m3_v1" /></label>
                <label><span class="mb-1 block text-[10px] text-white/42">Index version</span><input id="index-version" class="playground-control w-full" required placeholder="bge-m3-v1" /></label>
              </div>
            </fieldset>

            <button id="vector-settings-save" type="submit" class="w-full rounded-lg bg-mint-400 px-3 py-2.5 text-[11px] font-bold text-ink-950 transition hover:bg-mint-300">VectorTarget · EmbeddingProfile 저장</button>
            <p id="vector-settings-message" class="min-h-4 text-[10px] text-white/35" role="status" aria-live="polite">설정 조회 중</p>
          </form>
        </section>

        <section class="rounded-xl border border-white/7 bg-black/10 p-3 xl:col-span-5" aria-labelledby="vector-route-title">
          <div class="flex items-start justify-between gap-2">
            <div>
              <h3 id="vector-route-title" class="text-xs font-semibold text-white/75">프로젝트 검색 Route</h3>
              <p class="mt-1 text-[10px] leading-4 text-white/32">검증된 Snapshot·Vector binding 중 프로젝트가 실제 검색에 사용할 하나를 선택합니다.</p>
            </div>
            <span id="vector-route-revision" class="font-mono text-[9px] text-white/30">rev 0</span>
          </div>
          <div class="mt-3 grid gap-2 sm:grid-cols-[1fr_auto]">
            <input id="vector-route-project" class="playground-control w-full" placeholder="project_id" aria-label="Vector Route project ID" />
            <button id="vector-route-load" type="button" class="rounded-lg border border-white/10 px-3 py-2 text-[10px] font-semibold text-white/60 hover:bg-white/5">후보 조회</button>
          </div>
          <div id="vector-route-active" class="mt-2 rounded-lg border border-white/7 bg-black/15 p-3 text-[10px] leading-4 text-white/40">project_id를 입력해 현재 Route를 조회하세요.</div>
          <div class="mt-3 grid gap-2">
            <label><span class="mb-1 block text-[10px] text-white/42">Verified candidate binding</span><select id="vector-route-binding" class="playground-control w-full"><option value="">후보를 먼저 조회하세요</option></select></label>
            <div class="grid gap-2 sm:grid-cols-2">
              <label><span class="mb-1 block text-[10px] text-white/42">Routing mode</span><select id="vector-route-mode" class="playground-control w-full"><option value="pinned">Pinned</option><option value="managed_auto">Managed auto</option></select></label>
              <label><span class="mb-1 block text-[10px] text-white/42">변경 사유</span><input id="vector-route-reason" class="playground-control w-full" maxlength="2000" placeholder="선택 또는 rollback 이유" /></label>
            </div>
          </div>
          <div class="mt-3 grid grid-cols-2 gap-2">
            <button id="vector-route-apply" type="button" class="rounded-lg bg-mint-400 px-3 py-2 text-[10px] font-bold text-ink-950 hover:bg-mint-300">Route 적용</button>
            <button id="vector-route-clear" type="button" class="rounded-lg border border-danger-300/20 px-3 py-2 text-[10px] font-semibold text-danger-300 hover:bg-danger-300/5">Route 해제</button>
          </div>
          <p id="vector-route-message" class="mt-2 text-[10px] text-white/35" role="status" aria-live="polite">compatible → verified → routable → active 순서로 확인합니다.</p>
        </section>

        <section class="rounded-xl border border-white/7 bg-black/10 p-3 xl:col-span-4" aria-labelledby="reindex-title">
          <h3 id="reindex-title" class="text-xs font-semibold text-white/75">재인덱싱 실행</h3>
          <p class="mt-1 text-[10px] leading-4 text-white/32">활성 Repository Source에 실제 Index Job을 생성합니다.</p>
          <button id="reembed-button" type="button" class="mt-3 w-full rounded-lg bg-amber-300 px-3 py-2 text-[11px] font-bold text-ink-950 hover:brightness-105 disabled:opacity-50">등록 소스 전체 재임베딩</button>
          <p id="reembed-status" class="mt-2 min-h-4 text-[10px] text-white/35" role="status" aria-live="polite">실제 Repository Index Job을 생성합니다.</p>
        </section>

        <section class="rounded-xl border border-white/7 bg-black/10 p-3 xl:col-span-4" aria-labelledby="artifact-title">
          <div class="flex items-center justify-between gap-2">
            <div>
              <h3 id="artifact-title" class="text-xs font-semibold text-white/75">외부 Embedding Artifact</h3>
              <p class="mt-1 text-[9px] text-white/30">Package → 영속 원장·검색 Index</p>
            </div>
            <button id="embedding-artifact-refresh" type="button" class="rounded-md border border-white/10 px-2 py-1 text-[9px] text-white/50 hover:bg-white/5">새로고침</button>
          </div>
          <div id="embedding-artifact-list" class="mt-3 max-h-52 space-y-2 overflow-y-auto" aria-live="polite"><p class="text-[10px] text-white/30">동기화된 Package를 조회하고 있습니다.</p></div>
        </section>

        <section class="rounded-xl border border-white/7 bg-black/10 p-3 xl:col-span-4" aria-labelledby="index-job-title">
          <div class="flex items-center justify-between gap-2">
            <div>
              <h3 id="index-job-title" class="text-xs font-semibold text-white/75">Indexing Job</h3>
              <p class="mt-1 text-[9px] text-white/30">진행률·중단·오류 상태</p>
            </div>
            <span id="indexing-job-count" class="font-mono text-[9px] text-white/30">조회 중</span>
          </div>
          <div id="indexing-job-list" class="mt-3 max-h-52 space-y-2 overflow-y-auto" aria-live="polite"><p class="text-[10px] text-white/30">Indexing Job을 조회하고 있습니다.</p></div>
        </section>
      </div>
    </article>
  `;
}
