from typing import Optional
import os
import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import chromadb
from chromadb.utils import embedding_functions

# --- 설정 (Configuration) ---
TARGET_DIR = r"C:\Users\kaypa\Documents\HancomAI5"
DB_DIR = os.path.join(TARGET_DIR, "chroma_db")
COLLECTION_NAME = "source_code"

# Ollama API 엔드포인트 및 모델
OLLAMA_BASE_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text:latest"
LLM_MODEL = "qwen2.5-coder:3b"

app = FastAPI(title="VSsVscodeEX Real RAG Server", version="1")

# 외부 IP 통신 허용을 위한 CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- ChromaDB 및 Embedding 초기화 ---
ollama_ef = embedding_functions.OllamaEmbeddingFunction(
    url=f"{OLLAMA_BASE_URL}/api/embeddings",
    model_name=EMBED_MODEL,
)
chroma_client = chromadb.PersistentClient(path=DB_DIR)

# 컬렉션 가져오기 (미리 ingest.py를 돌렸다고 가정)
try:
    collection = chroma_client.get_collection(
        name=COLLECTION_NAME,
        embedding_function=ollama_ef
    )
except Exception as e:
    print(f"Warning: ChromaDB collection not found. Please run ingest.py first. ({e})")
    collection = None


# --- 스키마 정의 (mock_server와 동일) ---
class CodeContext(BaseModel):
    text: str
    file_path: str
    start_line: int
    end_line: int

class Ask(BaseModel):
    version: str = "1"
    question: str
    code_context: Optional[CodeContext] = None


def generate_llm_response(prompt: str) -> str:
    """Ollama API를 호출하여 LLM의 답변을 생성합니다."""
    payload = {
        "model": LLM_MODEL,
        "prompt": prompt,
        "stream": False
    }
    try:
        res = requests.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload, timeout=120)
        res.raise_for_status()
        return res.json().get("response", "")
    except Exception as e:
        return f"(Error: LLM API 호출 실패 - {e})"


@app.post("/generate")
def generate(req: Ask):
    # 오류 모드 (UI 테스트용)
    if "!error" in req.question:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "MODEL_UNAVAILABLE",
                    "message": "(mock) 모델이 응답하지 않습니다 — 오류 UI 테스트용 응답.",
                }
            },
        )

    retrieved_sources = []
    context_texts = []
    
    # 1. 사용자가 코드를 선택(드래그)한 상태라면, 가장 최우선 컨텍스트로 추가
    if req.code_context:
        context_texts.append(
            f"[User Selected Code: {req.code_context.file_path} (Lines {req.code_context.start_line}-{req.code_context.end_line})]\n"
            f"{req.code_context.text}\n"
        )
    
    # 2. ChromaDB에서 질문과 관련된 소스 코드 검색 (RAG)
    if collection is not None:
        try:
            results = collection.query(
                query_texts=[req.question],
                n_results=3 # 가장 관련성 높은 3개의 청크 반환
            )
            
            # ChromaDB 쿼리 결과를 API sources 스키마에 맞게 매핑
            if results and results['documents'] and results['documents'][0]:
                docs = results['documents'][0]
                metas = results['metadatas'][0]
                
                for doc, meta in zip(docs, metas):
                    context_texts.append(f"[Retrieved Source: {meta.get('file_path')} (Lines {meta.get('start_line')}-{meta.get('end_line')})]\n{doc}\n")
                    
                    retrieved_sources.append({
                        "type": "code" if meta.get('language') != 'md' else "doc",
                        "file_path": meta.get("file_path", "unknown"),
                        "start_line": meta.get("start_line", 0),
                        "end_line": meta.get("end_line", 0),
                        "snippet": doc[:200] + "..." if len(doc) > 200 else doc # 프론트 표시용 요약본
                    })
        except Exception as e:
            print(f"RAG Retrieval Error: {e}")

    # 3. 프롬프트 조합
    combined_context = "\n---\n".join(context_texts)
    
    prompt = (
        f"You are a helpful coding assistant. Answer the user's question based on the provided context.\n\n"
        f"Context:\n{combined_context}\n\n"
        f"User Question: {req.question}\n\n"
        f"Answer in Korean:"
    )
    
    # 4. LLM 호출
    import time
    start_time = time.time()
    answer = generate_llm_response(prompt)
    elapsed_ms = int((time.time() - start_time) * 1000)
    
    # 5. 결과 반환 (Mock 서버와 동일한 구조)
    return {
        "version": "1",
        "answer": answer,
        "sources": retrieved_sources,
        "meta": {"model": LLM_MODEL, "elapsed_ms": elapsed_ms, "rag_used": bool(retrieved_sources)}
    }

if __name__ == "__main__":
    import uvicorn
    # 외부 IP 허용을 위해 host="0.0.0.0" 으로 설정
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, debug=True)
