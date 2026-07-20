from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .schemas import (
    ChatRequest,
    ChatResponse,
    DocumentInput,
    IngestRequest,
    IngestResponse,
    LegacyIngestRequest,
    LegacyQueryRequest,
    SearchRequest,
    SearchResponse,
)
from .services import ChatService, EmbeddingService, ServiceError
from .text import chunk_text
from .vector_store import SQLiteVectorStore


app = FastAPI(
    title="VS Code AI Assistant Backend",
    version="2.0.0",
    description="VS Code extension, NVIDIA NIM, and a persistent vector store bridge.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin for origin in settings.cors_origins if "*" not in origin],
    allow_origin_regex=r"^(vscode-webview://.*|https?://(127\.0\.0\.1|localhost)(:\d+)?)$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

embedding_service = EmbeddingService(settings)
chat_service = ChatService(settings)
if settings.vector_db_provider != "sqlite":
    raise RuntimeError("현재 구현된 VECTOR_DB_PROVIDER는 sqlite입니다.")
vector_store = SQLiteVectorStore(settings.vector_db_path)


def _service_error(exc: ServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@app.get("/")
def root() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "vs-code-ai-assistant-backend",
        "api": "/v1",
        "docs": "/docs",
    }


@app.get("/v1/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "vs-code-ai-assistant-backend",
        "version": "2.0.0",
        "configuration": settings.public_status(),
        "vector_store": vector_store.stats(),
        "message": "백엔드 API 서버에서 응답중 입니다."
    }
# @app.get("/v1/health")
# def health_check():
#     return {
#         "status": "ok",

#     }

@app.post("/v1/documents/ingest", response_model=IngestResponse)
def ingest_documents(payload: IngestRequest) -> IngestResponse:
    chunks_stored = 0
    providers: set[str] = set()
    try:
        for document in payload.documents:
            text_chunks = chunk_text(
                document.text, settings.chunk_size, settings.chunk_overlap
            )
            embedded_chunks = []
            for index, content in enumerate(text_chunks):
                embedding = embedding_service.embed(content, input_type="passage")
                providers.add(embedding.provider)
                embedded_chunks.append(
                    {
                        "chunk_id": f"{document.document_id}#chunk-{index + 1}",
                        "content": content,
                        "embedding": embedding.vector,
                        "embedding_provider": embedding.provider,
                        "embedding_model": embedding.model,
                    }
                )
            chunks_stored += vector_store.replace_document(
                payload.project_id,
                document.document_id,
                document.path,
                document.language,
                embedded_chunks,
                document.metadata,
            )
    except ServiceError as exc:
        raise _service_error(exc) from exc
    return IngestResponse(
        project_id=payload.project_id,
        documents_received=len(payload.documents),
        chunks_stored=chunks_stored,
        embedding_provider=",".join(sorted(providers)) or settings.embedding_provider,
    )


@app.post("/v1/search", response_model=SearchResponse)
def search_documents(payload: SearchRequest) -> SearchResponse:
    try:
        embedding = embedding_service.embed(payload.query, input_type="query")
        results = vector_store.search(
            payload.project_id,
            embedding.vector,
            embedding.provider,
            embedding.model,
            payload.top_k,
        )
    except ServiceError as exc:
        raise _service_error(exc) from exc
    return SearchResponse(
        project_id=payload.project_id,
        query=payload.query,
        results=results,
        embedding_provider=embedding.provider,
    )


@app.post("/v1/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    # The frontend sends the workspace folder name as session_id. When a
    # separate project_id is omitted, use that incoming value as the VectorDB
    # project scope as well. No server-side session identifier is hard-coded.
    effective_project_id = payload.project_id or payload.session_id
    search_response = search_documents(
        SearchRequest(
            project_id=effective_project_id,
            query=payload.message,
            top_k=payload.top_k,
        )
    )
    try:
        answer, provider = chat_service.answer(
            payload.message, search_response.results, payload.history
        )
    except ServiceError as exc:
        raise _service_error(exc) from exc
    return ChatResponse(
        project_id=effective_project_id,
        session_id=payload.session_id,
        answer=answer,
        sources=search_response.results,
        metadata={
            "ai_provider": provider,
            "ai_model": settings.ai_model,
            "embedding_provider": search_response.embedding_provider,
            "top_k": payload.top_k,
            "session_scope": effective_project_id,
        },
    )


@app.delete("/v1/projects/{project_id}/documents")
def delete_project_documents(project_id: str) -> dict[str, Any]:
    return {"project_id": project_id, "deleted_chunks": vector_store.delete_project(project_id)}


@app.post("/extension/chat", response_model=ChatResponse, include_in_schema=False)
def extension_chat(payload: ChatRequest) -> ChatResponse:
    return chat(payload)


@app.post("/ingest", include_in_schema=False)
def legacy_ingest(payload: LegacyIngestRequest) -> dict[str, Any]:
    response = ingest_documents(
        IngestRequest(
            project_id="default",
            documents=[
                DocumentInput(
                    document_id=payload.document_id,
                    text=payload.text,
                    metadata=payload.metadata,
                )
            ],
        )
    )
    return {"message": "document ingested", **response.model_dump()}


@app.post("/search", include_in_schema=False)
def legacy_search(payload: LegacyQueryRequest) -> dict[str, Any]:
    response = search_documents(
        SearchRequest(project_id="default", query=payload.query, top_k=payload.top_k)
    )
    return {"query": payload.query, "results": [item.model_dump() for item in response.results]}


@app.post("/chat", include_in_schema=False)
def legacy_chat(payload: LegacyQueryRequest) -> dict[str, Any]:
    search_response = search_documents(
        SearchRequest(project_id="default", query=payload.query, top_k=payload.top_k)
    )
    try:
        answer, _provider = chat_service.answer(
            payload.query, search_response.results, []
        )
    except ServiceError as exc:
        raise _service_error(exc) from exc
    return {
        "query": payload.query,
        "answer": answer,
        "results": [item.model_dump() for item in search_response.results],
    }
