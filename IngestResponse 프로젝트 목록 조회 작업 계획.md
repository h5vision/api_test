# `/v1/IngestResponse` 프로젝트 목록 조회 작업 계획

> 문서 상태: BackendAPI 구현 반영 · Frontend 팀 전달 기준  
> 작성 기준일: 2026-07-24  
> 대상: BackendAPI 팀 · VS Code Extension 팀  
> 구현 상태: BackendAPI와 관리자 Endpoint 모니터링은 구현했다. VS Code
> Extension 구현은 Frontend 팀 범위이며 이 저장소에서 변경하지 않는다.

---

## 1. 작업 목적

VS Code Extension이 처음 실행될 때 Backend에 등록된 프로젝트와 해당
프로젝트를 인덱싱할 때 사용한 Git 버전을 조회한다.

Extension 사용자는 Sidebar에서 다음 정보를 확인할 수 있어야 한다.

- 현재 VS Code에 열려 있는 로컬 프로젝트명
- 로컬 프로젝트의 현재 Git commit SHA
- Backend에 인덱싱된 프로젝트명
- Backend 인덱스가 기준으로 삼은 Git commit SHA
- 로컬 프로젝트와 Backend 인덱스의 버전 일치 여부
- Backend 인덱스 상태와 마지막 갱신 시각

Extension의 `목록 새로고침` 버튼은 최초 실행과 동일한 API를 다시 호출한다.

---

## 2. 동결 대상 API

```http
GET /v1/IngestResponse
```

### 2.1 호출 시점

1. Extension 최초 활성화가 완료된 직후 한 번 호출한다.
2. 사용자가 Sidebar의 `목록 새로고침` 버튼을 누르면 다시 호출한다.
3. 자동 재호출 주기는 이번 작업에 포함하지 않는다.

### 2.2 요청

- Request body를 사용하지 않는다.
- 프로젝트 ID를 요청에 넣지 않는다.
- Backend에 등록된 프로젝트 목록 전체를 요청한다.

```http
GET /v1/IngestResponse HTTP/1.1
Host: api.blakeedenparker.cloud
Accept: application/json
X-Client-ID: <extension-installation-id>
```

`X-Client-ID`는 기존 Frontend 연결 추적 정책에 따라 전달할 수 있지만,
프로젝트 목록을 필터링하는 값으로 사용하지 않는다.

### 2.3 성공 응답

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

### 2.4 필드 정의

| Field | 필수 | 설명 |
|---|---:|---|
| `schema_version` | O | 응답 Schema 버전. 최초 값은 `1.0` |
| `request_id` | O | 요청 추적 ID |
| `projects` | O | Backend Project Registry 목록 |
| `project_id` | O | Backend 내부의 안정적인 프로젝트 식별자 |
| `project_name` | O | Sidebar에 표시할 프로젝트명 |
| `git_commit_sha` | 조건부 | 인덱싱 원본의 전체 Git commit SHA |
| `git_short_sha` | 조건부 | 화면 표시용 앞 7자리 SHA |
| `git_branch` | 조건부 | 인덱싱 원본의 Git branch |
| `git_dirty` | 조건부 | 인덱싱 당시 작업 트리에 미반영 변경이 있었는지 여부 |
| `git_committed_at` | 조건부 | 해당 commit의 작성 시각 |
| `active_snapshot_id` | 조건부 | 현재 검색에 사용되는 Snapshot ID |
| `index_status` | O | 현재 인덱스 상태 |
| `indexed_at` | 조건부 | 현재 인덱스가 준비된 시각 |
| `total` | O | `projects` 배열 항목 수 |
| `generated_at` | O | Backend가 목록을 생성한 시각 |

Git 저장소가 아닌 로컬 프로젝트처럼 Git 정보를 만들 수 없는 경우
`git_commit_sha`, `git_short_sha`, `git_branch`, `git_dirty`,
`git_committed_at`은 `null`을 허용한다. Field 자체를 임의로 생략하지 않는다.

### 2.5 빈 목록

등록되거나 인덱싱된 프로젝트가 없으면 오류가 아니라 `200 OK`를 반환한다.

```json
{
  "schema_version": "1.0",
  "request_id": "req_01...",
  "projects": [],
  "total": 0,
  "generated_at": "2026-07-24T03:20:00Z"
}
```

### 2.6 오류

PostgreSQL Project Registry에 연결할 수 없을 때 빈 배열을 정상 결과처럼
반환하지 않는다.

```http
503 Service Unavailable
```

```json
{
  "error": {
    "code": "PROJECT_REGISTRY_UNAVAILABLE",
    "message": "프로젝트 목록을 불러올 수 없습니다.",
    "retryable": true,
    "request_id": "req_01..."
  }
}
```

Extension은 기존에 표시한 목록을 즉시 삭제하지 않고 오류와
`마지막 정상 조회 시각`을 표시한다.

---

## 3. 프로젝트 목록 포함 기준

1. PostgreSQL `projects` 테이블을 목록의 기준 데이터로 사용한다.
2. VectorDB의 point를 직접 열거해서 프로젝트 목록을 만들지 않는다.
3. `active_snapshot_id`가 존재하는 프로젝트는 목록에 포함한다.
4. 초기 인덱싱 중이라 아직 활성 Snapshot이 없는 프로젝트도 진행 상황을
   보여주기 위해 목록에 포함할 수 있으며, 이때 `index_status`로 구분한다.
5. 삭제 처리된 프로젝트를 계속 노출하지 않는다.
6. 정렬은 `project_name ASC`, 동일 이름은 `project_id ASC`로 고정한다.

공개 상태 값은 다음으로 통일한다.

```text
not_indexed
queued
indexing
ready
partially_ready
failed
stale
```

현재 구현 일부에서 사용하는 `completed`는 Public API 응답에서 `ready`로
정규화한다. 내부 상태와 공개 상태의 매핑을 한 곳에서 관리한다.

---

## 4. 버전 비교 규칙

### 4.1 Git 프로젝트

Git 버전의 기준값은 전체 `commit SHA`다.

```text
local HEAD SHA == backend git_commit_sha
→ version_status = current

local HEAD SHA != backend git_commit_sha
→ version_status = different

backend project가 없음
→ version_status = not_indexed

양쪽 중 하나의 SHA가 없음
→ version_status = unknown
```

Branch 이름, 수정 날짜, short SHA만으로 버전 일치를 판정하지 않는다.

### 4.2 로컬 변경사항

로컬 작업 트리가 dirty여도 HEAD SHA 자체는 같을 수 있다. 따라서 Extension은
다음 두 상태를 별도로 표시한다.

- commit 기준 인덱스 일치 여부
- commit되지 않은 로컬 변경사항 존재 여부

예시:

```text
인덱스 버전 일치 · 로컬 수정사항 있음
```

### 4.3 Git이 아닌 프로젝트

Git 정보가 없는 프로젝트의 정확한 비교는 후속 파일 Manifest fingerprint
계약으로 처리한다. 이번 작업에서는 `unknown`으로 표시하고 수정 날짜만으로
최신 여부를 확정하지 않는다.

---

## 5. BackendAPI 작업 범위

### 5.1 Schema

`backend/schemas.py`에 다음 응답 모델을 추가한다.

- `IndexedProjectItem`
- `IndexedProjectListResponse`

현재 `backend/schemas.py`에는 `POST /v1/documents/ingest`의 응답으로 사용하는
`IngestResponse` 모델이 이미 존재한다. 합의한 URL은
`/v1/IngestResponse`로 유지하되, 새 목록 응답 모델까지 같은 Python class
이름으로 만들지 않는다.

### 5.2 Project Store

`backend/project_store.py`에 프로젝트 목록 조회 메서드를 추가한다.

예상 책임:

- `projects` 테이블을 한 번의 Query로 조회
- 안정적인 정렬 적용
- DB timestamp를 timezone-aware 값으로 반환
- DB 연결 실패를 `ProjectStoreError`로 전달
- `git_short_sha`는 저장하지 않고 전체 SHA에서 파생
- 기존 Project Registry에 `index_completed_at` nullable 컬럼을 추가하고,
  인덱싱 성공 시각을 기록

첫 구현에서는 현재 테이블에 존재하지 않는 GitHub Repository URL이나 Git tag를
응답에 추가하지 않는다. GitHub 조직 동기화 기능에서 해당 값을 저장하도록
Schema가 확장될 때 별도 계약으로 추가한다.

### 5.3 FastAPI Route

`backend/app.py`에 다음 Route를 추가한다.

```python
@app.get(
    "/v1/IngestResponse",
    response_model=IndexedProjectListResponse,
    tags=["Projects"],
)
```

Route의 책임:

- Project Store 목록 조회
- 내부 인덱스 상태를 Public 상태로 변환
- `request_id`, `generated_at`, `total` 생성
- DB 오류를 `503 PROJECT_REGISTRY_UNAVAILABLE`로 변환
- 응답에 `Cache-Control: no-store` 적용
- OpenAPI에 목적과 응답 예시 노출

경로는 대소문자를 구분하므로 Extension은 합의된
`/v1/IngestResponse`를 정확히 호출한다. 자동 lowercase alias는 이번
동결 범위에 포함하지 않는다.

### 5.4 API 사용 상태 기록

관리자 페이지의 Endpoint별 요청·응답 상태에서 확인할 수 있도록 모니터링 대상에
다음을 추가한다.

```text
GET /v1/IngestResponse
```

확인 항목:

- 요청 수신 여부
- 응답 반환 여부
- 마지막 HTTP status
- 마지막 성공 시각
- 마지막 응답 시간

---

## 6. VS Code Extension 팀 전달 범위

이 절은 Frontend 팀이 구현할 공개 계약이다. BackendAPI 팀은
`vision-frontend` Source를 직접 변경하지 않는다.

### 6.1 API Client

Extension의 공통 Backend `base_url`에 `/v1/IngestResponse`를 결합한다.
Production URL 전체를 Source에 직접 하드코딩하지 않는다.

```ts
export interface IndexedProjectItem {
  project_id: string;
  project_name: string;
  git_commit_sha: string | null;
  git_short_sha: string | null;
  git_branch: string | null;
  git_dirty: boolean | null;
  git_committed_at: string | null;
  active_snapshot_id: string | null;
  index_status:
    | "not_indexed"
    | "queued"
    | "indexing"
    | "ready"
    | "partially_ready"
    | "failed"
    | "stale";
  indexed_at: string | null;
}

export interface IndexedProjectListResponse {
  schema_version: "1.0";
  request_id: string;
  projects: IndexedProjectItem[];
  total: number;
  generated_at: string;
}
```

### 6.2 최초 실행

1. Extension 활성화
2. Backend base URL 확인
3. `GET /v1/IngestResponse` 호출
4. 로컬 Workspace 프로젝트명과 Git HEAD 조회
5. `project_id` 또는 합의된 Repository 식별자로 Backend 프로젝트 매칭
6. commit SHA 비교
7. Sidebar 갱신

목록 조회 실패가 Chat UI 전체를 종료시키지 않도록 기능별 오류로 처리한다.

### 6.3 목록 새로고침

- 최초 실행과 같은 API Client 함수를 재사용한다.
- 처리 중에는 버튼 중복 실행을 막는다.
- 성공하면 목록 전체를 새 응답으로 교체한다.
- 실패하면 기존 목록을 유지하고 재시도 안내를 표시한다.
- 마지막 정상 새로고침 시각을 표시한다.

### 6.4 Sidebar 표시 예시

```text
현재 Vision Assistant : overview
  Connected [ms] (backend /v1/health 지연시간 표기) frontend sidebar에 존재 api는 이미 /v1/health에서 얻어서 사용중임
  모델 : (AI Server 모델명 표기) 현재 frontend에서 표기할 API를 얻지 못한 상태

Backend Index
  protoFastAPI
  Indexed: 4ea031e
  상태: 최신

  protoFront
  Indexed: 82f117a
  상태: 다른 프로젝트

[목록 새로고침]
```

Backend의 `project_name`만으로 프로젝트를 확정 매칭하면 동명 프로젝트가 충돌할
수 있다. GitHub 기반 프로젝트는 `h5vision/repository-name` 형태의
`project_id`를 우선 사용한다.

---

## 7. 문서 반영 범위

구현과 함께 다음 문서를 동일 계약으로 수정한다.

- `API 통신 규약 V1 동결안.md`
- `Frontend API 규약.md`
- `docs/API_CONTRACT_V1.md`
- OpenAPI `/docs`

문서별로 URL 대소문자, HTTP method, nullable Field, 상태 값이 달라지지 않게
검증한다.

---

## 8. 검증 항목

### 8.1 Backend

- 프로젝트가 없을 때 `200`과 빈 `projects` 반환
- 프로젝트 한 개와 여러 개 조회
- 프로젝트 정렬 순서 확인
- 전체 commit SHA와 7자리 short SHA 확인
- Git 정보가 `null`인 프로젝트 직렬화
- 내부 `completed` 상태가 외부 `ready`로 변환되는지 확인
- PostgreSQL 장애 시 `503` 반환
- 응답의 `total`과 배열 길이 일치
- `Cache-Control: no-store` 확인
- `/docs`에 GET endpoint와 Schema 표시
- 기존 `POST /v1/documents/ingest`의 `IngestResponse`가 깨지지 않는지 확인

### 8.2 Extension

- 최초 활성화 시 한 번 조회
- 새로고침 버튼 재조회
- SHA 일치·불일치·없음 상태 표시
- 로컬 dirty 상태 별도 표시
- 빈 목록 UI
- `503`, timeout, 잘못된 JSON 처리
- 실패 시 기존 목록 유지
- 동일 이름의 서로 다른 `project_id` 처리

### 8.3 통합

```powershell
curl.exe -i "https://api.blakeedenparker.cloud/v1/IngestResponse" `
  -H "Accept: application/json" `
  -H "X-Client-ID: vscode-integration-test"
```

LAN 시험:

```powershell
curl.exe -i "http://192.168.0.7:8000/v1/IngestResponse" `
  -H "Accept: application/json" `
  -H "X-Client-ID: vscode-integration-test"
```

Cloudflare와 LAN 응답의 Schema 및 데이터가 같아야 한다.

---

## 9. 완료 조건

- [x] `GET /v1/IngestResponse`가 OpenAPI에 공개된다.
- [ ] Extension 최초 실행과 수동 새로고침에서 같은 API를 사용한다.
- [x] Backend Project Registry의 프로젝트명과 Git SHA가 정확히 반환된다.
- [ ] Extension이 로컬 SHA와 인덱스 SHA를 비교해 상태를 표시한다.
- [x] DB 장애를 빈 프로젝트 목록으로 오인하지 않는다.
- [x] 관리자 페이지에서 해당 Endpoint의 요청·응답 성공 여부를 확인할 수 있다.
- [x] 기존 문서 Ingest API와 Python `IngestResponse` 모델이 회귀하지 않는다.
- [ ] Backend, Extension, 계약 문서의 Field 이름과 nullable 규칙이 일치한다.

미완료 항목은 Frontend 팀 구현과 양 팀 통합 시험 후 체크한다.

---

## 10. 이번 작업에서 제외하는 범위

- GitHub `h5vision` 조직 전체 Repository 자동 clone
- GitHub Organization Webhook 처리
- push 발생 시 자동 재임베딩
- Git이 아닌 프로젝트의 Manifest fingerprint 비교
- 프로젝트 목록 pagination 및 검색
- Frontend가 인덱스를 직접 갱신하는 기능
- BackendAPI 팀이 VS Code Extension Source를 직접 수정하는 작업

위 항목은 이번 목록 조회가 안정화된 뒤 GitHub 조직 동기화·인덱싱 자동화
작업에서 별도로 진행한다.
