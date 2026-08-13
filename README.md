중요 사항 : "C:\Users\PC2412\Documents\HancomAI5\vision-frontend" 는 frontend 팀이 관리하는 VS Code Extension 프로젝트입니다. frontend 팀의 구현 단계와 구조를 파악하기 위한 ReadOnly이며, BackendAPI 팀은 이 Source를 직접 변경하거나 구현하지 않습니다. BackendAPI 팀은 공개 API 계약과 Swagger/OpenAPI 문서만 참고하여 구현합니다.

# VS Code AI Code Assistant Backend

독립된 VS Code 확장 프로그램에서 저장소를 업로드하고 질문하면, 이 FastAPI가 원문 보관·청킹·BGE-M3 임베딩·Qdrant 검색·프롬프트 조립을 수행한 뒤 선택된 backendAI 또는 NVIDIA 모델로 근거 기반 답변을 생성합니다.

## 2026-07-24 작업 보고 — 인덱싱 프로젝트 목록 조회

### 작업 목적

VS Code Extension이 처음 실행되거나 사용자가 Sidebar의
`목록 새로고침`을 선택했을 때, Backend에 등록된 프로젝트명과 인덱싱 기준
Git 버전을 조회할 수 있도록 Backend 공개 API를 추가했다.

Frontend 팀이 관리하는
`C:\Users\PC2412\Documents\HancomAI5\vision-frontend`의 Extension Source는
이번 작업에서 수정하지 않았다. BackendAPI 구현과 공개 계약, 관리자
모니터링까지만 이 저장소에서 담당한다.

### 구현 결과

```http
GET /v1/IngestResponse
Accept: application/json
```

- Request body와 `project_id` Query를 사용하지 않는다.
- PostgreSQL `projects` 테이블을 프로젝트 목록의 기준으로 사용한다.
- 프로젝트명, 전체 Git commit SHA, 표시용 short SHA, branch, Git dirty 여부,
  활성 Snapshot, 인덱스 상태와 마지막 인덱싱 완료 시각을 반환한다.
- `git_short_sha`는 전체 SHA에서 파생하며 버전 판정에는 사용하지 않는다.
- 내부 상태 `completed`는 공개 응답에서 `ready`로 변환한다.
- 프로젝트가 없으면 `200 OK`, `projects: []`, `total: 0`을 반환한다.
- PostgreSQL이 설정되지 않았거나 접근할 수 없으면 정상 빈 목록으로 위장하지
  않고 `503 PROJECT_REGISTRY_UNAVAILABLE`을 반환한다.
- 응답에는 `Cache-Control: no-store`, `X-Request-ID`,
  `X-API-Version: 1.0`이 적용된다.
- API 경로는 대소문자를 구분하므로 `/v1/IngestResponse`를 그대로 사용한다.

성공 응답 예시:

```json
{
  "schema_version": "1.0",
  "request_id": "req_01...",
  "projects": [
    {
      "project_id": "h5vision/protoFastAPI",
      "project_name": "protoFastAPI",
      "git_commit_sha": "4ea031ecb0f8f503e3d8ef27b01e53d771ab1234",
      "git_short_sha": "4ea031e",
      "git_branch": "main",
      "git_dirty": false,
      "git_committed_at": "2026-07-24T03:10:00Z",
      "active_snapshot_id": "snap_01...",
      "index_status": "ready",
      "indexed_at": "2026-07-24T03:15:00Z"
    }
  ],
  "total": 1,
  "generated_at": "2026-07-24T03:20:00Z"
}
```

### Backend 변경 파일

| 파일 | 작업 내용 |
|---|---|
| `backend/schemas.py` | `IndexedProjectItem`, `IndexedProjectListResponse` 추가 |
| `backend/project_store.py` | 목록 조회 Query와 `index_completed_at` 컬럼·갱신 로직 추가 |
| `backend/app.py` | `GET /v1/IngestResponse`, 상태 변환, 오류·활동 기록 추가 |
| `admin/src/main.ts` | Frontend 공개 Endpoint 상태 목록에 새 API 추가 |
| `verify_ingest_response.py` | 정상 목록·빈 목록·DB 장애 계약 검증 추가 |
| `verify_api_contract.py` | OpenAPI 경로·Schema 검증과 동결 해시 갱신 |

기존 `POST /v1/documents/ingest`가 사용하는 Python `IngestResponse` 모델은
유지했다. 새 프로젝트 목록 응답은 다른 모델명을 사용하므로 기존 문서
인제스트 응답과 충돌하지 않는다.

### 관리자 페이지 반영

관리자 페이지의 `API Endpoint` 영역에 다음 항목을 추가했다.

```text
GET /v1/IngestResponse — 인덱싱 프로젝트 목록
```

요청 수신 여부, 응답 반환 여부, 마지막 HTTP 상태, 성공 시각과 응답시간을 기존
Endpoint 활동 기록 방식으로 확인할 수 있다.

### 검증 결과

| 검증 | 결과 |
|---|---|
| Python `compileall` | 통과 |
| `verify_ingest_response.py` | 통과 |
| `verify_api_contract.py` | 통과 |
| 관리자 TypeScript·Vite build | 통과 |
| 임시 HTTP 서버 응답 Header·오류 형식 | 통과 |

동결된 OpenAPI SHA-256:

```text
d987383528bd7a3f84d2d824fc4b4c72e925a06ccde1cc95dfe9ad5f57857fe9
```

HTTP 검증용 임시 서버는 `127.0.0.1:8011`에서 실행 후 종료했으며 해당 포트에
남아 있는 프로세스는 없다. 검증 당시 8000번 서버는 실행 중이 아니었다.

### 실행 전 확인사항

Docker Compose 환경에서는 Backend 컨테이너에 PostgreSQL 설정과 비밀번호
파일을 주입한다. `python main.py`로 Windows에서 직접 실행하려면 로컬 `.env`에
다음 값이 별도로 필요하다.

```env
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=vision
POSTGRES_USER=vision
POSTGRES_PASSWORD=<실제 PostgreSQL 비밀번호>
```

PostgreSQL 설정이 없으면 `/v1/IngestResponse`는 의도적으로 `503`을 반환한다.
비밀번호는 README나 Git에 기록하지 않는다.

### Frontend 팀 인계사항

Frontend 팀은 다음 기능을 별도 Extension 프로젝트에 구현한다.

1. Extension 최초 활성화 시 `GET /v1/IngestResponse` 호출
2. Sidebar `목록 새로고침`에서 동일 API 재호출
3. 로컬 Workspace 이름과 Git HEAD SHA 수집
4. Backend 전체 `git_commit_sha`와 로컬 SHA 비교
5. `최신`, `버전 불일치`, `인덱싱되지 않음`, `비교 불가` 상태 표시
6. 로컬 dirty 상태를 commit 일치 여부와 별도로 표시
7. 조회 실패 시 기존 정상 목록을 유지하고 마지막 정상 조회 시각 표시

상세 계약은
[`IngestResponse 프로젝트 목록 조회 작업 계획.md`](<./IngestResponse 프로젝트 목록 조회 작업 계획.md>)와
[`Frontend API 규약.md`](<./Frontend API 규약.md>)를 기준으로 한다.

## 전체 구조

```text
VS Code Extension (별도 프로젝트/서버)
  ├─ 저장소 manifest + 분할 업로드
  ├─ 채팅 Webview와 model_id 선택
  └─ 공개 /v1 API만 호출
           │
           ▼
BackendAPI / FastAPI (/v1)
  ├─ Repository 원본 + Project Registry (PostgreSQL)
  ├─ Chunking + Ollama bge-m3:latest
  ├─ Vector Index (Qdrant)
  ├─ RAG 검색 + Prompt + sources[] 조립
  └─ Model Router
       ├─ backendAI FastAPI (192.168.0.12)
       └─ NVIDIA Cloud API
           │
           ▼
답변 + model/provider + 원본 경로/줄 번호/유사도
```

백엔드는 키나 내부 DB 구조를 프론트엔드에 노출하지 않습니다. 확장 프로그램은 공개 `/v1` API만 사용합니다.

## 프로젝트 구조

```text
Vision/
├─ backend/
│  ├─ app.py             # FastAPI 라우트와 RAG 처리 흐름
│  ├─ config.py          # .env 설정 로딩
│  ├─ schemas.py         # 공개 요청/응답 계약
│  ├─ services.py        # Ollama BGE-M3 임베딩 클라이언트
│  ├─ generation.py      # backendAI/NVIDIA 모델 라우터
│  ├─ connectivity.py    # frontend heartbeat와 최근 활동 저장
│  ├─ runtime_config.py  # 관리자 네트워크 설정 저장·적용
│  ├─ uploads.py         # 재개 가능한 저장소 분할 업로드
│  ├─ project_store.py   # PostgreSQL 원문/버전/청크 매핑
│  ├─ metadata_store.py  # PostgreSQL JSONB metadata 저장소
│  ├─ text.py            # 문서 청크 분할과 줄 번호 계산
│  └─ vector_store.py    # Qdrant 벡터 인덱스
├─ admin/                # 독립 관리자 웹 화면
├─ .env                  # 로컬 비밀 설정, Git 제외
├─ .env.example          # 공유 가능한 설정 템플릿
├─ main.py               # 백엔드 실행 진입점
├─ start_backend.ps1     # 올바른 가상환경 Python으로 실행
└─ verify_full_flow.py   # ingest → search → chat 통합 검증
```

## 1. Python 가상환경

PowerShell 활성화 스크립트는 Python 파일이 아닙니다. 다음처럼 `python3` 없이 실행해야 합니다.

```powershell
Set-Location "C:\Users\PC2412\Documents\HancomAI5"
& ".\.venv\Scripts\Activate.ps1"
```

절대 경로도 가능합니다.

```powershell
& "C:\Users\PC2412\Documents\HancomAI5\.venv\Scripts\Activate.ps1"
```

실행 정책 오류가 발생할 때만 현재 터미널 범위에서 허용합니다.

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
& "C:\Users\PC2412\Documents\HancomAI5\.venv\Scripts\Activate.ps1"
```

활성화하지 않고 가상환경 Python을 직접 실행해도 됩니다.

```powershell
& "C:\Users\PC2412\Documents\HancomAI5\.venv\Scripts\python.exe" `
  "C:\Users\PC2412\Documents\HancomAI5\Vision\main.py"
```

## 2. 백엔드 실행

프로젝트의 실행 스크립트가 상위 `.venv`를 자동으로 선택합니다.

```powershell
Set-Location "C:\Users\PC2412\Documents\HancomAI5\Vision"
.\start_backend.ps1
```

또는 활성화된 터미널에서 다음을 실행합니다.

```powershell
python main.py
```

- API: `http://127.0.0.1:8000/v1`
- Swagger UI: `http://127.0.0.1:8000/docs`
- 상태 확인: `http://127.0.0.1:8000/v1/health`

## 3. VS Code 확장 연결

확장 프로그램은 별도 프로젝트에서 관리합니다. API 주소는 내부망의 `http://192.168.0.7:8000`을 사용합니다. 프로젝트 폴더명을 `project_id`와 `session_id`로 전송하고, 생성 모델은 `GET /v1/models`에서 받은 공개 `model_id`만 전송합니다. provider URL이나 API key는 확장에 넣지 않습니다.

Extension 최초 실행과 Sidebar의 `목록 새로고침`은
`GET /v1/IngestResponse`를 사용합니다. 이 API는 PostgreSQL Project Registry의
프로젝트명, 전체 Git commit SHA, 표시용 short SHA, branch, 인덱스 상태와
완료 시각을 반환합니다. 정상 빈 목록은 `200`, Project Registry 장애는
`503 PROJECT_REGISTRY_UNAVAILABLE`로 구분합니다.

## API 계약

회의용 v1 동결 기준, 필드 제약, 오류 envelope, 호환성 정책과 release gate는
[`docs/API_CONTRACT_V1.md`](docs/API_CONTRACT_V1.md)에 정리되어 있습니다.
팀별 설명과 회의 진행용 체크리스트는
[`docs/API_V1_TEAM_BRIEFING_KO.md`](docs/API_V1_TEAM_BRIEFING_KO.md)를 사용합니다.
Frontend가 그대로 가져갈 TypeScript 타입과 호출 예시는
[`docs/frontend-api-v1.ts`](docs/frontend-api-v1.ts)입니다. 생성되는 Swagger와
`/openapi.json`이 기계 판독 가능한 최종 기준입니다.

### 상태 확인

```http
GET /v1/health
```

키 값은 반환하지 않고 provider와 설정 여부만 반환합니다.

### Frontend / BackendAI 연결 상태

VS Code 확장은 실행 중일 때 30초마다 heartbeat를 보냅니다. `client_id`는
하드코딩된 공용 값 대신 확장 인스턴스를 구분할 수 있는 값으로 설정하고,
`project_id`에는 현재 프로젝트 폴더명을 넣습니다.

```http
POST /v1/client-heartbeat
Content-Type: application/json

{
  "client_id": "vscode:Vision",
  "project_id": "Vision",
  "client_version": "0.1.0",
  "details": {"vscode_version": "1.105.0"}
}
```

`/v1/documents/ingest-with-metadata`, `/v1/metadata`, `/v1/chat`의 정상 요청도
frontend 최근 활동으로 기록됩니다. 이때 확장은 선택적으로
`X-Client-ID`와 `X-Client-Version` 헤더를 보내 동일한 client 행을 갱신할 수
있습니다. 헤더가 없으면 `vscode:{project_id}`를 사용합니다.

관리자 dashboard는 다음 상태 API를 30초마다 조회합니다.

```http
GET /v1/admin/connectivity
```

이 관리자 API는 공개 API 경로에서 호출할 수 없고, 관리자 Nginx의 내부
`/admin-api` 프록시를 통해서만 접근할 수 있습니다.

frontend는 마지막 신호가 75초 이내면 `online`, 180초 이내면 `stale`, 그
이후는 `offline`입니다. 아직 신호가 한 번도 없으면 `unknown`입니다.
BackendAI는 내부 Ollama `/api/tags`에 실제 요청을 보내고 `qwen2.5-coder:7b` 모델까지
확인하여 `online`, `degraded`, `offline`으로 표시합니다. 내부 IP가 포함되는
관리자 상태 응답은 공개하지 않으며 API key는 어떤 상태 응답에도 포함하지
않습니다.

관리자 화면의 **네트워크 대상 설정**에서는 Frontend와 BackendAI의 IPv4와
포트를 각각 수정할 수 있습니다. 저장값은 PostgreSQL
`runtime_network_settings`에 보관되어 컨테이너를 재시작해도 유지됩니다.
Frontend 주소는 TCP 도달성 확인에 사용하며, BackendAI 주소는 저장 직후부터
`/api/tags` 상태 확인과 `/api/chat` 생성 요청에 적용됩니다. `.env`의
`FRONTEND_HOST`, `FRONTEND_PORT`, `BACKENDAI_BASE_URL`은 DB 저장값이 아직 없을
때 사용하는 초기 기본값입니다.

### 문서 인덱싱

```http
POST /v1/documents/ingest
Content-Type: application/json

{
  "project_id": "my-project",
  "documents": [
    {
      "document_id": "file:///workspace/payment.py",
      "path": "src/payment.py",
      "language": "python",
      "text": "def retry_payment(): ...",
      "metadata": {"file_name": "payment.py"}
    }
  ]
}
```

동일한 `project_id`와 `document_id`를 다시 보내면 기존 청크를 교체하므로 파일 변경 후 재인덱싱할 수 있습니다.
`metadata`가 비어 있지 않으면 VectorDB 청크와 함께 PostgreSQL에도 `document` 범위 레코드로 upsert되며, 응답의 `metadata_records_stored`로 저장 건수를 확인할 수 있습니다.

프론트엔드가 프로젝트 공통 metadata를 문서 배열 밖에 보내는 경우에는 다음 전용 API를 사용합니다.

```http
POST /v1/documents/ingest-with-metadata
Content-Type: application/json

{
  "project_id": "default",
  "documents": [
    {
      "name": "string",
      "path": "string",
      "language": "string",
      "type": "string"
    }
  ],
  "metadata": {
    "additionalProp1": {}
  }
}
```

`project_id`는 내부 프로젝트 식별자로 사용합니다. 이 API는 요청당 문서를 최대 10,000개까지 받습니다. 이 형식에는 코드 본문인 `text`가 없으므로 임베딩은 실행하지 않습니다. 문서의 `name`은 PostgreSQL의 `document_id`로 저장하고 `path`, 선택적 `language`, `type`도 `frontend_documents` 테이블에 함께 등록합니다. 확장 타입의 선택 필드인 `size`, `modifiedTime`, `children`과 향후 추가 필드는 `details` JSONB에 보존합니다. 최상위 `metadata`는 `project` 범위로 한 번 저장합니다. 같은 `project_id`와 문서 `name`을 다시 보내면 문서 정보와 프로젝트 metadata를 갱신합니다. `documents_registered`는 등록 문서 수, `metadata_records_stored`는 metadata 저장 건수이며 `chunks_stored`는 `0`입니다. VectorDB 코드 검색이 필요하면 `text`를 포함하는 기존 `/v1/documents/ingest`를 사용합니다.

### 대규모 저장소 분할 업로드

한 번의 JSON 요청에 프로젝트 전체를 넣지 않습니다. 아래 순서로 최대 10,000개 파일을 manifest 여러 페이지와 16MiB part로 나눠 전송합니다. 프로젝트 전체 바이트 수에는 애플리케이션 상한을 두지 않으며, 각 part의 `Content-Range`와 SHA-256을 검증합니다. 개별 파일은 크기와 상관없이 원본 저장소에 수신하지만, 메모리 보호를 위해 텍스트 인덱싱 대상 파일 크기는 `MAX_INDEXABLE_FILE_BYTES`(기본 16MiB)로 별도 제한합니다.

```text
POST /v1/uploads
POST /v1/uploads/{upload_id}/manifest
PUT  /v1/uploads/{upload_id}/files/{file_id}/parts/{part_number}
POST /v1/uploads/{upload_id}/complete
GET  /v1/indexing-jobs/{job_id}
```

세션 생성 예시:

```json
{
  "schema_version": "1.0",
  "project_id": "Vision",
  "snapshot_id": "git-commit-or-generated-id",
  "document_count": 2,
  "total_bytes": 24576
}
```

manifest의 `file_id`는 URL에서 안전한 영문자·숫자·`. _ ~ -` 조합을 사용합니다. 각 파일에는 `relative_path`, `entry_type`, `size_bytes`, 선택적 `sha256`, `language_hint`를 보냅니다. part 요청에는 `Content-Range: bytes START-END/TOTAL`과 `X-Content-SHA256: <64자리 hex>` 또는 `Digest: sha-256=<base64>`를 넣습니다. 완료 응답의 `status_url`을 폴링하면 `files_received`, `documents_processed`, `chunks_stored`, `failed_documents`를 확인할 수 있습니다.

### Metadata 저장 및 조회

프론트엔드는 문서 인덱싱과 독립적으로 프로젝트, 세션, 문서 또는 사용자 정의 대상의 metadata를 보낼 수 있습니다.

```http
POST /v1/metadata
Content-Type: application/json

{
  "project_id": "Vision",
  "session_id": "Vision",
  "scope": "project",
  "source": "vscode-extension",
  "metadata": {
    "workspace_name": "Vision",
    "frontend_version": "0.1.0",
    "languages": ["python", "typescript"]
  }
}
```

`scope`은 `project`, `session`, `document`, `custom` 중 하나입니다. `project`와 `session`은 `entity_id`를 생략할 수 있고, `document`와 `custom`은 대상 식별자인 `entity_id`가 필수입니다. metadata는 JSON 객체이며 최대 500,000,000바이트(500MB), 최상위 키 200개까지 허용합니다. 실제 요청 크기는 JSON 구조 오버헤드와 클라이언트 메모리를 포함하므로 대용량 프로젝트는 분할 업로드를 사용합니다.

같은 `project_id + scope + entity_id`를 다시 보내면 기존 행의 JSONB payload를 교체하고 `updated_at`을 갱신합니다. 생성과 갱신 모두 `201 Created`로 저장된 전체 레코드를 반환합니다.

```http
GET /v1/projects/Vision/metadata?scope=project&limit=100
```

`scope`은 선택 사항이며 `limit`은 1~500입니다. 응답은 `project_id`와 최신순 `records` 배열입니다.

### VectorDB 검색

`/v1/search`는 Vision 내부 Qdrant를 직접 검색하지 않고 VectorDB 팀의
`rag_lab POST /search`를 프록시합니다.

```http
POST /v1/search
Content-Type: application/json

{
  "project_id": "my-project",
  "query": "결제 재시도는 어디에서 처리하나요?",
  "top_k": 5
}
```

### 근거 기반 AI 채팅

```http
POST /v1/chat
Content-Type: application/json

{
  "role": "user",
  "content": "결제 재시도 횟수를 알려줘"
}
```

Frontend의 최소 채팅 계약은 `role: "user"`와 `content`입니다. Backend는
`content`를 현재 질문인 `message`로 정규화하고, `project_id`가 없으면 현재
인덱스 목록에서 프로젝트를 해석하며 `session_id`가 없으면 결정적 fallback ID를
생성합니다. 프로젝트·세션·모델을 명시해야 하는 Client는 기존 `project_id`,
`session_id`, `model_id`, `history`, `stream` 필드를 같은 객체에 추가할 수 있고
기존 `message` 또는 `prompt` 요청도 계속 지원합니다. Backend는
`rag_lab GET /projects`로 프로젝트를 해석하고 `POST /prompt`에서 받은
`messages`와 `sources` 순서를 변경하지 않은 채 선택된 생성 모델에 전달합니다.
`has_evidence=false`이면 LLM을 호출하지 않고 `NO_EVIDENCE`를 반환합니다.
Vision FastAPI는 자체 벡터 검색, RAG 순위 결정 또는 Prompt 조립을 수행하지
않습니다. 이 세 작업의 소유자는 외부 `rag_lab`이며, Vision은 `/prompt`의
`messages`와 `sources` 순서를 보존해 모델 호출과 Frontend 응답 전달만 담당합니다.

`GET /v1/models`가 현재 선택 가능한 공개 모델 목록과 가용 상태를 반환합니다. 기본 모델은 `backendai-default`, Cloud 대안은 `nvidia-default`와 `groq-default`입니다. 현재 VS Code Extension 호환 응답의 최상위 필드는 `answer`, `source[]`, `metadata`입니다. 일반 `/v1/chat` 응답의 `metadata`는 빈 객체이며, 진단이 필요한 요청만 `"debug": true`를 보내면 요청 ID, 프로젝트 해석, 검색·모델·처리시간 정보를 받습니다. `source[]`는 `file`, `chunk`, `score`를 제공하며, 코드 근거 표시는 이 필드를 사용합니다. backendAI 장애 시 NVIDIA로 자동 전환하려면 서버에서만 `ALLOW_CLOUD_FALLBACK=true`를 명시해야 하며 기본값은 자동 전환하지 않는 것입니다. Groq는 자동 fallback 대상이 아니며 frontend가 `groq-default`를 명시적으로 선택할 때 사용합니다.

`backendai-default`는 내부적으로 `BACKENDAI_BASE_URL/api/chat`의 Ollama API를 호출하며 실제 모델은 BackendAI 모델 탐색 결과와 관리자 기본값으로 결정됩니다. v1의 신규 현재-turn 필드는 `role`과 `content`이며, 기존 `message`와 `prompt`도 호환용으로 받습니다. 최상위 `role`은 `user`만 허용하고 `assistant`·`system` 대화는 `history[]`에서만 사용합니다. `top_k`와 `reasoning_mode`는 구버전 호환을 위해 받지만 `rag_lab`이 검색과 evidence gate를 소유하므로 Vision은 무시합니다. `debug`는 기본값 `false`이며, `true`일 때만 JSON 응답 및 SSE `meta`/`done`의 상세 metadata를 반환합니다. `stream: false` 또는 생략은 기존 JSON 응답을 반환합니다. `stream: true`는 `meta(전송중) → status(추론중) → delta*(생각중) → done(답변중)` 순서의 SSE를 반환하며 도중 실패는 `error(답변 실패)` 이벤트로 전달합니다. 모든 SSE payload에는 `stage`, `label`, `simulated`, `progress_source`, `occurred_at`이 포함됩니다. `delta.text`는 화면에 이어 붙일 실제 답변 조각이고 `done.answer`는 최종 답변이므로, 상태 라벨과 답변 데이터는 함께 처리해야 합니다. 현재 진행 정보는 `simulated: true`, `progress_source: "vision-generator"`인 임시 값이고, 향후 AI Server가 실제 처리 단계를 제공하면 `simulated: false`, `progress_source: "ai-server"`로 전환합니다. 모든 응답은 `X-Request-ID`와 `X-API-Version: 1.0` 헤더를 반환합니다.

관리자 페이지의 `AI Provider CRUD · 자동 감지`에서는 추가 생성 서버를
등록할 수 있습니다. LAN Ollama는 IP/Host와 Port만 입력하면 `/api/tags`에서
모델을 감지하고, API Key 제공자는 OpenAI 호환 Base URL과 Bearer 또는
`X-API-Key` 인증을 입력하면 `/models`에서 모델을 감지합니다. `API 규격`을
`자동 감지`로 두면 Ollama를 먼저 확인하고 OpenAI 호환 API를 이어서 확인합니다.
감지된 모델은
`provider:{provider_id}:{model_name}` 형식의 공개 `model_id`로 `GET /v1/models`에
합쳐지며 같은 `/v1/chat`에서 즉시 사용할 수 있습니다. 관리 API는
`/v1/admin/ai-providers` CRUD와
`POST /v1/admin/ai-providers/{provider_id}/discover`이고, 관리자 reverse proxy를
통해서만 접근합니다. 저장 API Key는 PostgreSQL에 암호화되며 조회 응답에는
원문 대신 설정 여부와 끝 4자리 힌트만 포함됩니다.

같은 Provider 탐색 결과에서 Chat 모델과 Embedding 모델을 분리 저장합니다.
Embedding 모델은 관리자 Vector 설정의 드롭다운 또는 Provider 카드에서
선택할 수 있으며, 저장 전에 Ollama `/api/embed` 또는 OpenAI 호환
`/embeddings`를 호출해 실제 벡터 차원을 검증합니다. Provider 연결은 Base URL
뿐 아니라 `IP/Host + Port + HTTPS 여부`로도 추가·조회·수정·삭제할 수 있고,
선택된 Provider의 암호화된 API Key를 임베딩 요청에도 재사용합니다. 현재 활성
임베딩 Provider는 다른 Provider를 선택하기 전에는 비활성화하거나 삭제할 수
없습니다.

### VectorDB Provider Registry (MVP)

관리자 페이지의 `VectorDB Provider 등록 · 자동 감지`에서 VectorDB 이름,
DB 종류, Host/Port, TLS, 저장 Namespace/Collection, 임베딩 모델 경로와 사용할
Embedding 모델 목록을 등록합니다. 관리 API는
`GET/POST /v1/admin/vector-databases`,
`PUT/DELETE /v1/admin/vector-databases/{provider_id}` 및
`POST /v1/admin/vector-databases/{provider_id}/discover`입니다. 최초 조회 시 현재
활성 Qdrant 설정을 Registry에 자동 등록하며, 등록정보를 삭제해도 실제 VectorDB
데이터는 삭제하지 않습니다.

MVP 감지기는 Qdrant collection API, Weaviate readiness/schema API, Chroma
heartbeat/collection API, Milvus REST collection API를 구분합니다. pgvector와
Custom 대상도 등록할 수 있지만 현재는 별도 Adapter가 필요하다는 상태로
표시합니다. 실제 Vision RAG의 저장·검색 Adapter는 현재 Qdrant만 활성화할 수
있습니다. 따라서 다른 DB가 `online`으로 감지되어도 `adapter_available=false`이면
기존 Qdrant 런타임을 자동 교체하지 않습니다. Embedding 모델은 VectorDB가
발견하는 값이 아니라 AI Provider Registry에서 감지된 모델을 복수 선택해 연결하며,
모델·차원·Index version이 바뀌면 새 Namespace와 재인덱싱이 필요합니다.

저장 위치 방식은 `원격/별도 VectorDB 서버`와 `API Server Local 경로`로
구분합니다. 원격 모드는 Host/Port/TLS를 사용하고, 로컬 모드는 컨테이너의
`/vector-db-local` 아래 상대 경로를 사용합니다. Windows 호스트에서 실제로
연결되는 루트는 `.env`의 `VECTOR_DB_LOCAL_ROOT`이며 Compose가 읽기·쓰기로
마운트합니다. 다른 PC로 옮길 때는 이 환경변수만 새 PC 경로로 바꾸면 됩니다.
경로 이탈(`..`)이나 마운트 루트 밖의 절대경로는 API가 거부합니다. 로컬
SQLite 파일은 Vision Adapter를 사용할 수 있지만, Qdrant·Chroma 저장 폴더는
파일을 직접 읽지 않고 해당 서버 프로세스/Adapter를 통해 접근해야 합니다.

`POST /v1/admin/ai-providers/scan-cloud` 또는 관리자 페이지의
`.env Cloud Key 다시 탐색` 버튼은 `.env`에 설정된 NVIDIA/Groq 키를
브라우저에 노출하지 않고 각 `/models` 카탈로그를 조회합니다. 임베딩 모델이
발견된 Cloud Provider만 PostgreSQL Provider CRUD에 등록하고 API Key는 기존
Fernet master key로 암호화합니다. 카탈로그 노출 여부와 실제 사용 가능 여부는
다를 수 있으므로, 모델 선택 시 `/embeddings`로 시험 벡터를 생성해 차원을
확인한 모델만 Vector 설정에 반영할 수 있습니다. NVIDIA의 `input_type` 및
`truncate` 확장 필드와 표준 OpenAI Embedding 요청을 모두 지원합니다.

관리자 페이지의 `Cloud API Key 등록` 폼 또는
`POST /v1/admin/ai-providers/cloud-credentials`를 사용하면 서버를 재시작하거나
`.env`를 수정하지 않고 Cloud Provider를 추가할 수 있습니다. NVIDIA, Groq,
OpenAI는 공급자만 선택하면 서버가 해당 카탈로그 Base URL을 결정하므로 관리자는
API Key만 입력하면 됩니다. 키를 저장하기 전에 `/models` 호출이 성공해야 하며,
응답 모델은 Chat과 Embedding으로 분리해 Provider CRUD에 즉시 등록됩니다. API
Key는 발급처나 API 주소를 스스로 표현하지 않는 불투명한 문자열이므로 Custom
OpenAI-compatible Provider는 예외적으로 Base URL과 인증 방식을 함께 입력해야
합니다. 등록된 Provider는 같은 화면의 기존 CRUD에서 자동 감지·수정·삭제할 수
있고, 비밀키 원문은 어떤 조회 응답에도 반환하지 않습니다.

`POST /v1/admin/ai-providers/scan-ollama` 또는 관리자 페이지의
`등록 PC의 Ollama 자동 탐색` 버튼은 FastAPI가 알고 있는 범위만 병렬 탐색합니다.
Docker API Server의 Windows host(`host.docker.internal`), 현재 설정된 AI Model
Server, 활성화된 Frontend Client IP의 `11434` 포트가 대상입니다. 전체 LAN을
무차별 스캔하지 않습니다. 발견된 Ollama 모델 중 `completion` capability가 있는
모델은 Chat 목록에, `bge-m3`, `nomic-embed-text`처럼 `embedding` capability가
있는 모델은 Embedding 목록에 등록합니다. 다른 PC의 Ollama는 기본적으로
`127.0.0.1:11434`에만 bind되므로 해당 PC에서 `OLLAMA_HOST=0.0.0.0:11434`를
설정하고 Windows 방화벽에서 Backend API Server의 접근을 허용해야 탐색됩니다.
Ollama Cloud 모델은 해당 PC에서 로그인한 뒤 `*-cloud` 모델을 pull하면 로컬
`/api/chat` 계약 그대로 사용할 수 있고, `ollama.com`을 직접 호출할 때는
Provider CRUD에서 Base URL과 Bearer API Key를 등록합니다.

## 환경 변수

`.env`는 Git에서 제외됩니다. 공유할 때는 `.env.example`만 사용합니다.

| 변수 | 설명 |
|---|---|
| `NVIDIA_API_KEY` | NVIDIA API 키 |
| `AI_BASE_URL`, `AI_MODEL` | NVIDIA Chat Completions 주소와 실제 모델 |
| `BACKENDAI_BASE_URL`, `BACKENDAI_MODEL` | 내부 backendAI Ollama 주소와 실제 생성 모델 |
| `DEFAULT_MODEL_ID` | frontend에 노출하는 기본 공개 모델 ID |
| `ALLOW_CLOUD_FALLBACK` | 내부 모델 장애 시 NVIDIA 자동 전환 허용 여부 |
| `GROQ_API_KEY` | 서버 전용 Groq 인증 키. frontend 응답에는 노출하지 않음 |
| `GROQ_BASE_URL`, `GROQ_MODEL` | Groq OpenAI 호환 API 주소와 실제 생성 모델 |
| `GROQ_PUBLIC_MODEL_ID` | frontend에 노출하는 Groq 공개 ID. 기본값 `groq-default` |
| `AI_PROVIDER_MASTER_KEY`, `AI_PROVIDER_MASTER_KEY_FILE` | 관리자 입력 Provider API Key를 암호화하는 Fernet master key 또는 secret 파일. Compose는 persistent secret을 자동 생성 |
| `EMBEDDING_BASE_URL` | Ollama 주소. Docker에서는 `http://host.docker.internal:11434` |
| `EMBEDDING_MODEL` | `bge-m3:latest` |
| `EMBEDDING_DIMENSION` | BGE-M3 벡터 차원 `1024` |
| `QDRANT_URL`, `QDRANT_COLLECTION` | Qdrant 주소와 버전별 collection |
| `RAG_LAB_BASE_URL` | VectorDB 팀 rag_lab API 주소. 기본값 `http://192.168.0.12:8200` |
| `RAG_LAB_TOKEN` | 선택형 `X-VSS-Token` 인증 값 |
| `RAG_LAB_TIMEOUT_SECONDS` | `/search`, `/prompt` 서버 간 타임아웃. 기본값 60초 |
| `INDEX_VERSION` | embedding/index 계약 버전 |
| `UPLOAD_ROOT`, `UPLOAD_PART_SIZE_BYTES` | 업로드 보관 위치와 part 크기 |
| `POSTGRES_HOST`, `POSTGRES_PORT` | metadata PostgreSQL 접속 주소와 포트 |
| `POSTGRES_DB`, `POSTGRES_USER` | metadata 데이터베이스와 사용자 |
| `POSTGRES_PASSWORD` | 로컬 실행용 PostgreSQL 비밀번호 |
| `POSTGRES_PASSWORD_FILE` | Docker secret 파일 경로. 설정 시 비밀번호 대신 파일을 읽음 |
| `ALLOW_LOCAL_FALLBACK` | 해시 기반 로컬 임베딩/응답 fallback 허용. 운영 기본값은 `false` |
| `CHUNK_SIZE` | 문서 청크 최대 문자 수. 지원 언어는 함수·클래스·Markdown 제목 경계를 우선 사용 |
| `CHUNK_OVERLAP` | 인접 청크 중첩 문자 수 |

## 검증

외부 API 비용 없이 전체 왕복 흐름을 검증합니다.

```powershell
& "C:\Users\PC2412\Documents\HancomAI5\.venv\Scripts\python.exe" verify_full_flow.py
```

AI 호출 없이 동결된 스키마 자체만 검증하려면 다음을 실행합니다.

```powershell
docker exec vision-api-1 python /workspace/verify_api_contract.py
docker exec vision-api-1 python /workspace/verify_repository_contract.py
```

NVIDIA API까지 실제로 검증하려면 다음을 사용합니다.

```powershell
& "C:\Users\PC2412\Documents\HancomAI5\.venv\Scripts\python.exe" verify_full_flow.py --live
```

## 현재 저장 방식과 확장 지점

- PostgreSQL은 Source Registry, 프로젝트/snapshot manifest, 원문, 세대별 청크 본문과 외부 vector point 매핑을 보관합니다.
- Qdrant는 `bge-m3:latest` 1024차원 벡터와 `project_id`, `snapshot_id`, `generation_id` 검색 payload를 보관합니다. collection과 `INDEX_VERSION`은 embedding 계약이 바뀔 때 함께 올립니다.
- 업로드 원본은 `UPLOAD_ROOT`에 세션별로 보관됩니다. manifest와 part 메타데이터는 파일별로 분리해 대규모 프로젝트에서도 단일 상태 JSON이 커지지 않습니다.
- frontend는 하나의 `/v1/chat`과 공개 `model_id`만 사용하며, BackendAI/NVIDIA/Groq 주소와 인증은 Model Router가 관리합니다.
- 기존 `/ingest`, `/search`, `/chat`, `/extension/chat` 경로는 호환용으로 유지되며 신규 확장은 `/v1` 경로를 사용합니다.

## Repository Source와 세대별 Vector 인덱싱

호스트의 `PROJECT_DB_LOCAL_ROOT`는 API 컨테이너의 `/project-db`에 읽기 전용으로
마운트됩니다. Source Registry에는 Windows 절대 경로 대신 루트 기준 상대 경로만
저장합니다. 현재 등록한 기준 Source는 다음과 같습니다.

| 항목 | 값 |
|---|---|
| `source_id` | `git-github-h5vision-fest-api` |
| `project_id` | `h5vision/fest-api` |
| `root_relative_path` | `fest-api` |
| Git remote | `https://github.com/h5vision/fest-api.git` |
| branch / revision | `main` / Git HEAD |

관리자용 Source 등록과 인덱싱 시작 API는 Dashboard 내부 프록시만 호출할 수
있습니다.

```http
POST /v1/admin/repository-sources
POST /v1/admin/repository-sources/{source_id}/index
GET  /v1/admin/repository-sources
```

인덱싱 요청은 즉시 `202 Accepted`와 `job_id`를 반환합니다. 진행 상태는 공개
상태 API로 확인합니다.

```http
GET /v1/indexing-jobs/{job_id}
```

처리 순서는 `Git HEAD 검사 → Snapshot/Manifest 영구 저장 → 코드 Chunking →
bge-m3 임베딩 → Qdrant 신규 Generation 저장 → PostgreSQL/Qdrant 개수 검증 →
Active Generation 원자적 전환`입니다. 중간에 실패하면 신규 Generation만
정리하고 기존 Active Generation은 유지합니다. 동일 Git revision은
`force=false`일 때 다시 임베딩하지 않습니다.

VS Code Extension이 활성 snapshot을 읽을 때 사용하는 공개 API는 다음과
같습니다. `project_id`에 `/`가 포함되어도 FastAPI의 `path` converter가
처리합니다.

```http
GET /v1/repositories
GET /v1/repositories/{source_id}/tree
GET /v1/projects/h5vision/fest-api/tree
GET /v1/projects/h5vision/fest-api/file?path=fastapi/applications.py
GET /v1/projects/h5vision/fest-api/index-validation
GET /v1/IngestResponse
POST /v1/search
POST /v1/chat
```

`/v1/repositories`는 Backend checkout의 Git HEAD와 활성 DB Snapshot
revision을 비교해 `current`, `different`, `not_indexed`, `unavailable`
상태를 반환합니다. `/v1/repositories/{source_id}/tree`는 Git HEAD의 추적
구조를, `/v1/projects/{project_id}/tree`는 RAG가 사용하는 활성 Snapshot
구조를 반환합니다. 두 API를 구분한 Frontend 전달 계약은
`docs/FRONTEND_GIT_AND_INDEXED_PROJECT_SIDEBAR_API_GUIDE_KO.md`에 정리되어
있습니다. `file`은 활성 snapshot에 PostgreSQL 원문이 있는 텍스트 파일만
반환합니다.
`index-validation`은 기존 Vision 로컬 Generation 진단 API로 유지됩니다.
현재 `/v1/IngestResponse`, `/v1/search`, `/v1/chat`의 프로젝트 목록·검색·프롬프트는
외부 `rag_lab` 계약을 기준으로 동작합니다.

## Docker Compose 인프라

Windows 11의 Docker Desktop(WSL2 Linux 컨테이너)에서 다음 구조로 실행합니다.

```text
LAN client
        |
Traefik :80 / :8000
        |
Traefik load balancer
        |
Granian + FastAPI (api=1..6)
        |
+-------------+------------+
|             |            |
Postgres    Qdrant       Redis Stream
                           |
                    index worker (1..4)
       Docker 내부망 전용
```

필요한 이미지는 로컬에 없으면 registry에서 자동으로 받습니다. API 소스는 이미지에 복사하지 않고 clone한 현재 디렉터리를 `/workspace`에 읽기 전용 bind mount합니다. `api-deps` 초기화 컨테이너가 Python 의존성을 `api_python_deps` 볼륨에 설치하므로 호스트의 `.venv`를 Linux 컨테이너에서 재사용하지 않습니다. PostgreSQL, Qdrant, Redis는 호스트 포트를 공개하지 않습니다.

### Clone 후 가장 짧은 실행

Docker Desktop 또는 Docker Engine의 Linux container 환경에서 Repository 루트에서 실행합니다.

```powershell
docker compose config --quiet
docker compose up -d
docker compose ps
```

기본 실행은 다음을 자동 처리합니다.

- PostgreSQL, Redis, Qdrant 내부 credential 최초 생성 및 Docker volume 보존
- Python dependency 설치
- `alembic upgrade head`
- 관리자 Vite build
- FastAPI, worker, Traefik, 관리자 Nginx 기동

호스트 IP, clone 경로, 외부 AI/RAG 주소를 고정하지 않아도 `/v1/health`, `/docs`, 관리자 페이지까지 기동됩니다. AI Server와 RAG Server가 없으면 해당 연결만 offline/setup-required로 표시되며 FastAPI 자체는 계속 응답합니다.

다른 bind IP나 외부 서비스를 지정할 때만 Compose 환경 파일을 만듭니다.

```powershell
Copy-Item .\compose.env.example .\compose.env
notepad .\compose.env
```

`PROJECT_PATH=.`이 기본값이므로 clone 위치를 바꿔도 수정할 필요가 없습니다. 다른 Repository 루트를 노출할 때만 `PROJECT_DB_LOCAL_ROOT`에 Docker가 접근 가능한 절대 경로를 입력합니다. Compose의 `type: bind`는 `docker run --mount type=bind`와 같은 기능입니다. Docker의 `-m` 옵션은 마운트가 아니라 메모리 제한입니다.

Qdrant, Redis, PostgreSQL 비밀번호는 값이 없으면 `runtime_secrets` Docker 볼륨에 최초 실행 시 안전한 난수로 자동 생성됩니다. 운영자가 값을 공급하려면 `compose.env`에 `QDRANT_API_KEY`와 `REDIS_PASSWORD`를 지정합니다.

```powershell
docker compose --env-file .\compose.env up -d --scale api=2
```

| 접속 주소 | Traefik 대상 |
|---|---|
| `http://<SERVER_IP>:8000` | 모든 FastAPI 경로 |
| `http://<SERVER_IP>/v1/*`, `/docs`, `/openapi.json`, `/redoc` | FastAPI |
| `http://<SERVER_IP>/` | 관리자 Nginx |

Tailwind CSS 관리자 화면은 `http://<SERVER_IP>`에 연결됩니다. 서버 PC에서는 `http://127.0.0.1:4180`으로도 확인할 수 있으며 포트는 `ADMIN_PREVIEW_PORT`로 변경할 수 있습니다.

개발자용 sLLM Playground는
`http://<SERVER_IP>/playground`에서 사용합니다. 모델 dropdown은
`GET /v1/models`의 공개 `model_id`를 사용하며 BackendAI, NVIDIA와 Groq를 선택할 수
있습니다. 실행 요청은 실제 frontend와 동일한 `POST /v1/chat`을 사용하고,
답변의 provider, 실제 사용 모델, request ID, 소요시간과 RAG `sources[]`를
표시합니다. Playground 요청은 `X-Client-Type: admin-playground`로 구분되어
VS Code frontend heartbeat 상태에는 포함되지 않습니다.

관리자 `System Status` 화면은 일반 요청·응답 메타데이터 로그와 별도로
`Frontend Chat 감사 로그`를 제공합니다. `POST /v1/chat`의 질문, 최종 AI 답변,
프로젝트·세션·Client ID, 선택 모델, RAG Source 수, 처리시간과 오류를
PostgreSQL에 기록하며 최근 7일 동안 항목별 최대 20,000자만 보관합니다.
Frontend가 전달한 `context`와 `history`는 민감한 코드·대화가 중복 저장되지
않도록 본문 대신 각각 글자 수와 항목 수만 기록합니다. 조회 경로
`GET /v1/admin/chat-audit-logs`는 공개 OpenAPI 계약에서 숨겨져 있고
`admin-web` 내부 프록시를 통과한 요청만 허용합니다.

Client ID가 없는 Extension이 처음 `POST /v1/chat`을 보내면 별도
`Frontend 최초 연결 · ID 등록 로그`에 등록 시도와 처리 결과를 같은
`request_id`로 기록합니다. 신규 등록이면 서버가 발급한 `fcli_*` ID,
최초 연결 시각, Client 이름, 설치별 Instance ID, Extension 버전, 접속 IP와
식별 방법이 남습니다. Frontend가 사람을 구분할 표시 이름을 제공하려면
선택 Header `X-Client-User`를 함께 보내야 하며, 서버는 OS 사용자명을 임의로
추측하지 않습니다. 한글 표시 이름은 HTTP Header 제약 때문에
`encodeURIComponent(userName)` 값으로 보내면 서버가 복원합니다.
`GET /v1/admin/frontend-registration-logs` 역시
`admin-web` 내부 프록시 전용입니다.

관리자 개요 화면의 AI/Vector 설정은 PostgreSQL
`runtime_service_settings`에 저장됩니다. Groq 사용 여부, Base URL, 실제 모델명과
기본 공개 `model_id`는 저장 즉시 Model Router에 적용됩니다. Qdrant host/port,
collection, embedding model과 index version은 실행 중인 인덱싱 작업의 일관성을
보호하기 위해 저장 후 `restart_required`로 표시되며 다음 API 프로세스 시작부터
적용됩니다. API key는 관리자 응답이나 브라우저에 전달하지 않고 `.env` 또는
secret 파일에서만 읽습니다.

`등록 Source 전체 재임베딩`은 Mock이 아닙니다. 관리자 전용 Repository Source
목록에서 활성 Source를 조회한 뒤 각 Source에
`POST /v1/admin/repository-sources/{source_id}/index`와 `force=true`를 보내 실제
세대별 인덱싱 Job을 생성합니다. Vector 설정에 재시작 대기가 있으면 이전
collection에 잘못 인덱싱하지 않도록 버튼 실행을 차단합니다.

관리자 UI 소스는 `admin/`에 있으며 Tailwind CSS v4와 Vite로 빌드합니다. Compose의 `admin-build`가 소스를 읽기 전용 bind mount하여 `admin_dist` 볼륨에 결과를 만들고, `admin-web` Nginx가 그 볼륨을 제공합니다. 소스 변경 후 관리자 화면만 다시 빌드하려면 다음을 실행합니다.

```powershell
docker compose --env-file .\compose.env up --force-recreate admin-build
docker compose --env-file .\compose.env up -d --force-recreate admin-web
```

구성 검사와 실행:

```powershell
docker compose --env-file .\compose.env config --quiet
docker compose --env-file .\compose.env up -d
docker compose --env-file .\compose.env ps
```

확인 주소는 `http://127.0.0.1:8000/v1/health` 또는 `http://<SERVER_IP>:8000/v1/health`입니다. Traefik은 `api` 컨테이너의 Docker label을 자동 감지하고 같은 서비스의 여러 replica에 요청을 분산합니다. `X-Backend-Instance` 응답 헤더로 실제 응답한 replica를 확인할 수 있습니다.

내부망 클라이언트는 `http://<SERVER_IP>:8000`으로도 같은 API를 호출할 수 있습니다. 호스트 8000 포트는 Python 프로세스에 직접 연결하지 않고 Traefik의 `direct-api` entrypoint가 받아 `vision-api` 서비스로 프록시합니다. `/v1/health`, `/v1/documents/ingest`, `/v1/documents/ingest-with-metadata`, `/v1/metadata`, `/v1/projects/{project_id}/metadata`, `/v1/search`, `/v1/chat` 요청·응답 형식은 도메인 경로와 동일합니다.

업로드 원본은 `api_data` 볼륨, 문서 원문·버전·metadata는 PostgreSQL, BGE-M3 벡터는 Qdrant에 영구 저장됩니다. PostgreSQL, Qdrant, Redis는 Docker 내부 `data` 네트워크에서만 접근할 수 있습니다.

인덱싱은 FastAPI 프로세스 안의 임시 Background Task가 아니라 Redis Stream과
별도 `worker` 컨테이너에서 실행됩니다. Consumer Group, 작업 dedupe key,
PostgreSQL의 Source별 활성 Job unique index를 함께 사용하므로 API나 Worker가
여러 개여도 같은 인덱싱 작업을 동시에 실행하지 않습니다. Worker가 중단된
작업은 lease 만료 뒤 다른 Worker가 회수하며, 큐 전송 자체가 실패한 Job은
`failed/queue_unavailable`로 바뀌어 다시 요청할 수 있습니다.

VS Code Extension의 첫 `POST /v1/chat`은 미등록 Client라도 즉시 자동 등록하고
같은 요청을 처리합니다. 이후 응답의 `X-Client-ID`를 저장해 재사용하며, 관리자가
해당 Client의 `enabled=false`를 지정하면 다음 요청부터 `403`으로 차단됩니다.
NAT 환경에서 IP는 여러 사용자가 공유하거나 Docker gateway로 변환될 수 있으므로
접근 제어 키로 사용하지 않습니다. Extension은 설치마다 생성해 보관하는
`X-Client-Instance-ID`를 보내야 합니다. 자세한 계약과 운영 절차는
`docs/CLIENT_AUTO_REGISTRATION_AND_AUTOSCALING_KO.md`를 참고합니다.

Compose 자동 증설기는 요청량, 활성 요청, API CPU, API 메모리, Redis 대기열을
30초마다 확인합니다. 기본 범위는 API 1~6개, Worker 1~4개이며 scale-down은
5회 연속 저부하와 90초 cooldown 뒤에만 수행합니다.

```powershell
# 1회 판단만 출력
.\tools\autoscale_compose.ps1 -Once -DryRun

# 숨김 프로세스로 계속 실행
.\tools\start_autoscaler.ps1

# 종료
.\tools\stop_autoscaler.ps1
```
