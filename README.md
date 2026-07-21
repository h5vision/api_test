# VS Code AI Code Assistant

VS Code 확장 프로그램에서 현재 코드를 인덱싱하고 질문하면 FastAPI가 VectorDB를 검색한 뒤 NVIDIA NIM으로 근거 기반 답변을 생성하는 실행 가능한 예제입니다.

## 전체 구조

```text
VS Code Extension (frontend/)
  ├─ 현재 파일 읽기
  ├─ 채팅 Webview
  └─ HTTP JSON 요청
           │
           ▼
FastAPI Backend (/v1)
  ├─ 문서 청크 분할
  ├─ NVIDIA Embeddings
  ├─ SQLite Vector Store
  ├─ PostgreSQL Metadata Store
  └─ NVIDIA Chat Completions
           │
           ▼
답변 + 근거 파일 + 유사도 점수
```

백엔드는 키나 내부 DB 구조를 프론트엔드에 노출하지 않습니다. 확장 프로그램은 공개 `/v1` API만 사용합니다.

## 프로젝트 구조

```text
Vision/
├─ backend/
│  ├─ app.py             # FastAPI 라우트와 처리 흐름
│  ├─ config.py          # .env 설정 로딩
│  ├─ schemas.py         # 요청/응답 계약
│  ├─ services.py        # NVIDIA AI 및 Embedding 클라이언트
│  ├─ metadata_store.py  # PostgreSQL JSONB metadata 저장소
│  ├─ text.py            # 문서 청크 분할
│  └─ vector_store.py    # SQLite 영구 VectorDB
├─ frontend/
│  ├─ src/extension.ts   # VS Code 확장과 Webview
│  ├─ package.json
│  └─ tsconfig.json
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

## 3. VS Code 확장 실행

```powershell
Set-Location .\frontend
npm install
npm run compile
```

프로젝트 루트 `Vision`을 VS Code로 연 뒤 `F5`를 누르고 `Run Backend + Extension`을 선택합니다. 새 Extension Development Host에서 명령 팔레트를 열어 다음 명령을 실행합니다.

1. `Hancom AI: 현재 파일 인덱싱`
2. `Hancom AI: 채팅 열기`
3. 프로젝트 코드에 관해 질문

확장 설정:

- `hancomAi.backendUrl`: 기본값 `http://127.0.0.1:8000`
- `hancomAi.projectId`: 비어 있으면 첫 워크스페이스 폴더명
- `hancomAi.topK`: 답변에 사용할 검색 청크 수

## API 계약

### 상태 확인

```http
GET /v1/health
```

키 값은 반환하지 않고 provider와 설정 여부만 반환합니다.

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

`project_id`는 내부 프로젝트 식별자로 사용합니다. 이 API는 요청당 문서를 최대 10,000개까지 받습니다. 이 형식에는 코드 본문인 `text`가 없으므로 임베딩은 실행하지 않습니다. 문서의 `name`은 PostgreSQL의 `document_id`로 저장하고 `path`, `language`, `type`도 `frontend_documents` 테이블에 함께 등록합니다. 최상위 `metadata`는 `project` 범위로 한 번 저장합니다. 같은 `project_id`와 문서 `name`을 다시 보내면 문서 정보와 프로젝트 metadata를 갱신합니다. `documents_registered`는 등록 문서 수, `metadata_records_stored`는 metadata 저장 건수이며 `chunks_stored`는 `0`입니다. VectorDB 코드 검색이 필요하면 `text`를 포함하는 기존 `/v1/documents/ingest`를 사용합니다.

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

`scope`은 `project`, `session`, `document`, `custom` 중 하나입니다. `project`와 `session`은 `entity_id`를 생략할 수 있고, `document`와 `custom`은 대상 식별자인 `entity_id`가 필수입니다. metadata는 JSON 객체이며 최대 500,000,000바이트(500MB), 최상위 키 200개까지 허용합니다. Cloudflare의 업로드 제한은 metadata가 아닌 전체 HTTP 요청 크기에 적용되므로 실제 공개 도메인 요청은 JSON 구조 오버헤드를 포함해 이보다 작아야 합니다.

같은 `project_id + scope + entity_id`를 다시 보내면 기존 행의 JSONB payload를 교체하고 `updated_at`을 갱신합니다. 생성과 갱신 모두 `201 Created`로 저장된 전체 레코드를 반환합니다.

```http
GET /v1/projects/Vision/metadata?scope=project&limit=100
```

`scope`은 선택 사항이며 `limit`은 1~500입니다. 응답은 `project_id`와 최신순 `records` 배열입니다.

### VectorDB 검색

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
  "message": "결제 재시도 횟수를 알려줘",
  "session_id": "my-project",
  "top_k": 5,
  "history": []
}
```

`session_id`에는 프론트엔드가 현재 프로젝트 폴더명을 넣어 전송합니다. 백엔드는 이 값을 변경하거나 하드코딩하지 않고 그대로 응답하며, `project_id`가 생략되면 같은 값을 VectorDB 프로젝트 검색 범위로 사용합니다. `project_id`를 별도로 보내면 해당 값이 검색 범위로 우선 사용됩니다.

응답에는 `answer`, `sources`, `metadata`가 포함됩니다. `sources`에는 원본 파일, 청크 ID, 텍스트, 유사도 점수가 들어갑니다.

## 환경 변수

`.env`는 Git에서 제외됩니다. 공유할 때는 `.env.example`만 사용합니다.

| 변수 | 설명 |
|---|---|
| `NVIDIA_API_KEY` | NVIDIA API 키 |
| `AI_BASE_URL` | Chat Completions API base URL |
| `AI_MODEL` | 답변 생성 모델 |
| `EMBEDDING_BASE_URL` | Embeddings API base URL |
| `EMBEDDING_MODEL` | 검색 임베딩 모델 |
| `VECTOR_DB_PATH` | SQLite DB 파일 경로 |
| `POSTGRES_HOST`, `POSTGRES_PORT` | metadata PostgreSQL 접속 주소와 포트 |
| `POSTGRES_DB`, `POSTGRES_USER` | metadata 데이터베이스와 사용자 |
| `POSTGRES_PASSWORD` | 로컬 실행용 PostgreSQL 비밀번호 |
| `POSTGRES_PASSWORD_FILE` | Docker secret 파일 경로. 설정 시 비밀번호 대신 파일을 읽음 |
| `ALLOW_LOCAL_FALLBACK` | 외부 API 실패 시 로컬 검색 응답 허용 |
| `CHUNK_SIZE` | 문서 청크 최대 문자 수 |
| `CHUNK_OVERLAP` | 인접 청크 중첩 문자 수 |

## 검증

외부 API 비용 없이 전체 왕복 흐름을 검증합니다.

```powershell
& "C:\Users\PC2412\Documents\HancomAI5\.venv\Scripts\python.exe" verify_full_flow.py
```

NVIDIA API까지 실제로 검증하려면 다음을 사용합니다.

```powershell
& "C:\Users\PC2412\Documents\HancomAI5\.venv\Scripts\python.exe" verify_full_flow.py --live
```

## 현재 저장 방식과 확장 지점

- 기본 VectorDB는 별도 서버 없이 동작하는 영구 SQLite 저장소입니다.
- 프로젝트와 문서 ID가 분리되어 여러 VS Code 워크스페이스를 저장할 수 있습니다.
- Qdrant, Weaviate, Pinecone 등으로 교체할 때는 `backend/vector_store.py`와 설정 provider를 확장하면 공개 API 계약은 그대로 유지할 수 있습니다.
- 기존 `/ingest`, `/search`, `/chat`, `/extension/chat` 경로는 호환용으로 유지되며 신규 확장은 `/v1` 경로를 사용합니다.

## Docker Compose 인프라

Windows 11의 Docker Desktop(WSL2 Linux 컨테이너)에서 다음 구조로 실행합니다.

```text
Cloudflare DNS / Access
        |
Cloudflare Tunnel
        |
cloudflared (Docker connector)
        |
Traefik :8080 (Docker 내부 entrypoint)
        |
Granian + bind-mounted FastAPI :8000
        |
+---------+--------+
|         |        |
Postgres  Qdrant   Redis
       Docker 내부망 전용
```

로컬의 `traefik:latest`와 `mecodia/python-base-granian:v1` 이미지를 재사용합니다. 프로젝트 소스는 이미지에 복사하지 않고 `PROJECT_PATH`를 `/workspace`에 읽기 전용 bind mount합니다. `api-deps` 초기화 컨테이너가 Python 의존성을 `api_python_deps` 볼륨에 설치하므로 Windows용 `.venv`를 Linux 컨테이너에서 재사용하지 않습니다. PostgreSQL, Qdrant, Redis는 호스트 포트를 공개하지 않습니다.

먼저 Compose 환경 파일을 만들고 예제 비밀값을 실제 값으로 바꿉니다.

```powershell
Copy-Item .\compose.env.example .\compose.env
notepad .\compose.env
```

`PROJECT_PATH`는 Docker Desktop이 접근 가능한 Windows 절대 경로를 `/` 구분자로 입력합니다. Compose의 `type: bind`는 `docker run --mount type=bind`와 같은 기능입니다. Docker의 `-m` 옵션은 마운트가 아니라 메모리 제한입니다.

Cloudflare Zero Trust에서 Tunnel을 만든 뒤 connector 환경은 **Docker**로 선택합니다. 발급된 원격 관리형 Tunnel 토큰만 `compose.env`의 `CLOUDFLARE_TUNNEL_TOKEN`에 저장합니다. Public Hostname 세 개는 모두 다음 origin으로 지정합니다.

Cloudflare에서 내려받은 Docker 명령이 `cloudflared 커넥터 설치.txt`에 있다면 토큰을 복사하지 않고 다음 스크립트로 Traefik과 connector만 시작할 수 있습니다. 파일은 Tunnel 토큰을 포함하므로 `.gitignore`에 등록되어 있습니다.

```powershell
.\start_cloudflare_connector.ps1
```

`compose.env`의 Cloudflare, Qdrant, Redis 비밀값을 교체한 뒤 전체 스택을 시작할 때는 다음을 사용합니다. PostgreSQL 비밀번호는 `runtime_secrets` Docker 볼륨에 최초 실행 시 안전한 난수로 자동 생성되며 Compose 환경 파일에 기록되지 않습니다.

```powershell
.\start_cloudflare_connector.ps1 -FullStack
```

| Public hostname | Origin service | Traefik 대상 |
|---|---|---|
| `api.blakeedenparker.cloud` | `http://web:80` | FastAPI |
| `dashboard.blakeedenparker.cloud` | `http://web:80` | Access 적용 전에는 404 |
| `index.blakeedenparker.cloud` | `http://web:80` | 현재 FastAPI, 추후 웹 서비스로 교체 |

`web`은 Traefik 컨테이너의 Docker 네트워크 alias이며 Cloudflare에 이미 저장된 origin 설정과 호환됩니다. 별도 컨테이너가 아닙니다. `dashboard.blakeedenparker.cloud`는 Cloudflare Access의 SSO/MFA 정책으로 보호한 뒤 Tunnel 라우터를 활성화해야 합니다. Access 적용 전에는 LAN의 443 라우터만 존재합니다. VS Code 확장 API에 Access를 적용할 때는 브라우저 로그인이 아니라 Service Token 또는 애플리케이션 인증을 사용합니다.

Tailwind CSS 관리자 화면은 `https://dashboard.blakeedenparker.cloud`에 연결됩니다. 로컬에서는 `http://127.0.0.1:4173`으로도 확인할 수 있으며 포트는 `ADMIN_PREVIEW_PORT`로 변경할 수 있습니다. 공개 운영 시 Cloudflare Access의 SSO/MFA 정책을 반드시 적용합니다.

관리자 UI 소스는 `admin/`에 있으며 Tailwind CSS v4와 Vite로 빌드합니다. Compose의 `admin-build`가 소스를 읽기 전용 bind mount하여 `admin_dist` 볼륨에 결과를 만들고, `admin-web` Nginx가 그 볼륨을 제공합니다. 소스 변경 후 관리자 화면만 다시 빌드하려면 다음을 실행합니다.

```powershell
docker compose --env-file .\compose.env up --force-recreate admin-build
docker compose --env-file .\compose.env up -d --force-recreate admin-web
```

내부 DNS로 직접 접근해야 한다면 프론트엔드 팀 PC의 관리자 PowerShell에서 hosts 파일에 도메인을 등록할 수 있습니다.

```powershell
Add-Content -Path "$env:SystemRoot\System32\drivers\etc\hosts" `
  -Value "`n192.168.0.7 api.blakeedenparker.cloud dashboard.blakeedenparker.cloud index.blakeedenparker.cloud"
```

구성 검사와 실행:

```powershell
docker compose --env-file .\compose.env config --quiet
docker compose --env-file .\compose.env up -d
docker compose --env-file .\compose.env ps
```

확인 주소는 `https://api.blakeedenparker.cloud/v1/health`입니다. Cloudflare 경로에서는 TLS가 Cloudflare Edge에서 종료되고 Tunnel 내부에서는 `cloudflared`가 Traefik의 전용 HTTP entrypoint로 전달합니다. LAN의 80/443 직접 접근도 유지되며, 이 경로는 별도 인증서를 설치하기 전까지 Traefik 기본 인증서를 사용합니다.

내부망 클라이언트는 `http://192.168.0.7:8000`으로도 같은 API를 호출할 수 있습니다. 호스트 8000 포트는 Python 프로세스에 직접 연결하지 않고 Traefik의 `direct-api` entrypoint가 받아 `vision-api` 서비스로 프록시합니다. `/v1/health`, `/v1/documents/ingest`, `/v1/documents/ingest-with-metadata`, `/v1/metadata`, `/v1/projects/{project_id}/metadata`, `/v1/search`, `/v1/chat` 요청·응답 형식은 도메인 경로와 동일합니다.

문서 임베딩은 SQLite Vector Store를 사용해 `api_data` 볼륨에 영구 저장되고, 프론트엔드 metadata는 PostgreSQL의 `frontend_metadata` JSONB 테이블에 저장됩니다. 두 DB와 Qdrant/Redis는 Docker 내부 `data` 네트워크에서만 접근할 수 있습니다.
