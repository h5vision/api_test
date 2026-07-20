# VS Code AI Code Assistant Backend

이 저장소는 VS Code 확장 프로그램에서 채팅 기반 AI 코드 어시스턴트를 구현할 때, Backend가 어떻게 VectorDB와 임베딩 모델을 연결해 주고받아야 하는지 보여주기 위한 FastAPI 예제입니다. README를 읽은 개발자는 이 서버를 기준으로 프론트엔드 확장 프로그램이 호출할 API 구조를 바로 구성할 수 있습니다.

## 1. 목적

이 프로젝트의 목표는 다음을 지원하는 것입니다.

- VS Code 확장 프로그램에서 사용자가 질문을 입력할 수 있다.
- Backend가 질문을 임베딩한다.
- VectorDB에서 유사 문서를 검색한다.
- 검색 결과를 바탕으로 응답을 생성하거나, 확장 프로그램이 추가로 처리할 수 있도록 제공한다.

즉, 확장 프로그램과 Backend 간의 기본 통신 구조를 FastAPI로 구현한 예제입니다.

## 2. 제공되는 API

### 2.1 상태 확인

- GET /
- 서버가 정상 동작 중인지 확인하는 용도입니다.

### 2.2 문서 인덱싱

- POST /ingest
- 텍스트를 임베딩하고 VectorDB에 저장합니다.

요청 예시:

```json
{
  "document_id": "doc-001",
  "text": "결제 실패 시 재시도는 어디에서 처리하나요?",
  "metadata": {
    "source": "faq"
  }
}
```

### 2.3 검색

- POST /search
- 사용자의 질문을 임베딩한 뒤, VectorDB에서 유사 문서를 검색합니다.

요청 예시:

```json
{
  "query": "결제 실패 재시도 처리",
  "top_k": 3
}
```

### 2.4 채팅 응답용 엔드포인트

- POST /chat
- 검색 결과를 바탕으로 확장 프로그램이 AI 응답을 구성할 수 있도록 기본 응답 포맷을 제공합니다.

## 3. 실행 방법

### 3.1 의존성 설치

```bash
pip install fastapi uvicorn
```

### 3.2 서버 실행

```bash
python main.py
```

서버는 기본적으로 http://0.0.0.0:8000 에서 실행됩니다.

## 4. 환경 변수

다음 환경 변수를 사용할 수 있습니다.

- EMBEDDING_BASE_URL: 임베딩 모델 API 주소
- EMBEDDING_API_KEY: 임베딩 모델 API 키
- EMBEDDING_MODEL: 사용 모델명
- EMBEDDING_PROVIDER: openai 또는 ollama
- VECTOR_DB_BASE_URL: VectorDB API 주소
- VECTOR_DB_COLLECTION: 사용할 컬렉션명

## 5. 동작 방식

1. 확장 프로그램이 질문을 Backend로 전송한다.
2. Backend가 질문 텍스트를 임베딩한다.
3. VectorDB에서 관련 문서를 검색한다.
4. 검색 결과를 확장 프로그램에 반환한다.
5. 확장 프로그램은 이 결과를 기반으로 AI 응답을 구성한다.

## 6. 확장 프로그램 연동 포인트

VS Code 확장 프로그램에서는 다음 방식으로 이 API를 호출하면 됩니다.

- 사용자의 채팅 입력을 /search 또는 /chat에 전송
- 검색 결과를 UI에 표시
- 필요 시 추가 프롬프트와 결합해 답변 생성

## 7. 참고

- 현재 기본 구현은 외부 임베딩 API나 VectorDB가 없어도 로컬 fallback으로 동작합니다.
- 실제 서비스에서는 OpenAI, Ollama, Qdrant, Weaviate, Pinecone 등으로 교체할 수 있습니다.

## 1. 문서 목적

이 문서는 다음 내용을 정리하기 위해 작성되었습니다.

- API가 무엇인지
- HTTP API의 기본 구성 요소가 무엇인지
- 프론트엔드와 Backend가 어떻게 통신하는지
- 일반 요청과 장기 실행 요청이 어떻게 다른지
- Streaming API가 왜 필요한지
- 기능과 API를 1:1로 매칭하는 원칙이 무엇인지

## 2. 핵심 원칙

### 2.1 1:1 매칭 원칙

- 서버 처리가 필요한 사용자 기능은 공개 HTTP API 하나로 연결한다.
- 하나의 장기 실행 작업에 포함되는 실시간 상태 기능은 Streaming Event Type 하나로 연결한다.
- VS Code 내부에서만 처리 가능한 기능은 Extension Local Command로 연결한다.
- Backend 내부 처리 기능은 Internal Service Contract로 연결한다.

### 2.2 구현 독립성 원칙

API 계약은 특정 DB, Vector DB, Queue, Cache, Storage 방식에 종속되지 않도록 설계됩니다. 실제 구현이 바뀌더라도 다음 계약은 유지되어야 합니다.

- Endpoint
- Request Schema
- Response Schema
- 상태값
- 오류 코드
- Streaming Event 형식
- ID 관계

## 3. API 기본 규칙

### 3.1 Base URL

- 기본 경로: /v1
- 예시: POST /v1/generate

### 3.2 Content Type

- 일반 요청: application/json
- Streaming 요청: text/event-stream

### 3.3 공통 Header

- X-Client-Version
- X-Request-ID
- 필요 시 Idempotency-Key

> 현재 데모 범위에서는 인증 기능이 제외되어 있으며, 향후 인증이 추가될 경우 Authorization 헤더를 보조적으로 사용할 수 있습니다.

### 3.4 식별자 규칙

- project_id
- job_id
- generation_id
- question_id
- answer_id
- revision_id
- proposal_id
- source_id

식별자는 내부 DB의 Primary Key 구조를 노출하지 않는 불투명 문자열로 다루는 것이 원칙입니다.

## 4. 주요 공개 API

### 4.1 인덱싱 관련

| 기능 | Method | Endpoint |
|---|---|---|
| 인덱싱 실행 | POST | /v1/ingest |
| 인덱싱 진행 상태 조회 | GET | /v1/ingest/{job_id} |
| 인덱싱 상태 조회 | GET | /v1/projects/{project_id}/index-status |
| 인덱스 갱신 | POST | /v1/projects/{project_id}/reindex |
| 프로젝트 브리핑 조회 | GET | /v1/projects/{project_id}/briefing |

### 4.2 질문 및 생성 관련

| 기능 | Method | Endpoint |
|---|---|---|
| 질문 입력 및 생성 요청 | POST | /v1/generate |
| 생성 상태 및 답변 스트리밍 | GET | /v1/generations/{generation_id}/events |
| 생성 작업 취소 | DELETE | /v1/generations/{generation_id} |
| 질문 수정 및 새 답변 생성 | POST | /v1/questions/{question_id}/revisions |
| 답변 재생성 | POST | /v1/answers/{answer_id}/regenerations |
| 출처 조회 | GET | /v1/generations/{generation_id}/sources |

### 4.3 연관 파일 관련

| 기능 | Method | Endpoint |
|---|---|---|
| 연관 파일 조회 | POST | /v1/related-files |

## 5. 요청 예시

### 질문 생성 요청 예시

```http
POST /v1/generate
Content-Type: application/json
Accept: application/json

{
  "project_id": "project_01",
  "question": {
    "content": "결제 실패 시 재시도는 어디에서 처리해?"
  }
}
```

### 스트리밍 상태 수신 예시

```http
GET /v1/generations/{generation_id}/events
Accept: text/event-stream
```

## 6. 처리 흐름

1. 사용자가 질문을 입력한다.
2. Extension이 Backend API로 요청을 전달한다.
3. Backend가 프로젝트 검색, 근거 확인, 답변 생성을 수행한다.
4. 생성 과정은 Streaming Event로 상태를 전달한다.
5. 최종 결과와 출처 정보를 조회한다.

## 7. 참고 사항

- 이 문서는 API 계약 중심으로 작성되었으며, 내부 저장소 구조나 DB 구현 방식은 명시하지 않는다.
- API는 URL, Method, Request/Response Schema, 상태값, 오류 코드, Streaming Event 형식을 기준으로 설계된다.
- 기능 변경이 있어도 공개 계약이 유지되도록 관리하는 것이 핵심이다.

## 8. 관련 문서

- [VS Code AI Code Assistant API 1&1.docx](VS%20Code%20AI%20Code%20Assistant%20API%201&1.docx)
- [VS Code AI Code Assistant API uni.docx](VS%20Code%20AI%20Code%20Assistant%20API%20uni.docx)

