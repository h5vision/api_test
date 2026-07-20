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
