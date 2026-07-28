import os
import glob
from pathlib import Path
import chromadb
from chromadb.utils import embedding_functions

# --- 설정 (Configuration) ---
TARGET_DIR = r"C:\Users\kaypa\Documents\HancomAI5"
DB_DIR = os.path.join(TARGET_DIR, "chroma_db")
COLLECTION_NAME = "source_code"
OLLAMA_URL = "http://localhost:11434/api/embeddings"
EMBED_MODEL = "nomic-embed-text:latest"

# 무시할 디렉토리 및 확장자 설정
IGNORE_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".idea", ".vscode", "chroma_db"}
SUPPORTED_EXTENSIONS = {
    ".py", ".js", ".ts", ".html", ".css", ".md", ".json", 
    ".java", ".cpp", ".c", ".h", ".cs", ".go", ".rs", ".php"
}
CHUNK_SIZE_LINES = 100
OVERLAP_LINES = 20

def get_files_to_process(root_dir):
    files_to_process = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        # 무시할 디렉토리 필터링 (in-place 변경으로 os.walk의 순회 방지)
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        
        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()
            if ext in SUPPORTED_EXTENSIONS:
                files_to_process.append(os.path.join(dirpath, filename))
    return files_to_process

def chunk_file(file_path):
    chunks = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        ext = os.path.splitext(file_path)[1].lower()
        language = ext[1:] if ext else "unknown"
        
        for i in range(0, len(lines), CHUNK_SIZE_LINES - OVERLAP_LINES):
            chunk_lines = lines[i:i + CHUNK_SIZE_LINES]
            if not chunk_lines:
                break
                
            chunk_text = "".join(chunk_lines)
            start_line = i + 1
            end_line = i + len(chunk_lines)
            
            # 메타데이터를 VSCode 확장 프로그램 스키마와 최대한 맞춤
            metadata = {
                "file_path": file_path,
                "language": language,
                "start_line": start_line,
                "end_line": end_line
            }
            chunks.append((chunk_text, metadata))
            
    except Exception as e:
        print(f"파일 읽기 오류 ({file_path}): {e}")
        
    return chunks

def main():
    print(f"--- 데이터 수집(Ingestion) 시작 ---")
    print(f"대상 디렉토리: {TARGET_DIR}")
    
    # 1. 대상 파일 스캔
    files = get_files_to_process(TARGET_DIR)
    print(f"발견된 소스 파일 수: {len(files)}")
    
    # 2. 청킹(Chunking) 진행
    all_documents = []
    all_metadatas = []
    all_ids = []
    
    for idx, file_path in enumerate(files):
        chunks = chunk_file(file_path)
        for chunk_idx, (text, meta) in enumerate(chunks):
            all_documents.append(text)
            all_metadatas.append(meta)
            # 고유 ID 생성 (파일경로_시작줄_끝줄)
            doc_id = f"{file_path}_{meta['start_line']}_{meta['end_line']}"
            all_ids.append(doc_id)
            
    print(f"총 생성된 청크(Chunk) 수: {len(all_documents)}")
    
    if not all_documents:
        print("저장할 문서가 없습니다. 종료합니다.")
        return

    # 3. ChromaDB 및 Ollama Embedding 연동
    print(f"ChromaDB({DB_DIR}) 초기화 및 컬렉션 로드 중...")
    
    # Ollama Embedding 함수 설정
    # 참고: chromadb 버전 호환을 위해 명시적으로 Ollama API 호출
    ollama_ef = embedding_functions.OllamaEmbeddingFunction(
        url=OLLAMA_URL,
        model_name=EMBED_MODEL,
    )
    
    client = chromadb.PersistentClient(path=DB_DIR)
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME, 
        embedding_function=ollama_ef
    )
    
    # DB에 일괄 저장 (Upsert)
    BATCH_SIZE = 100
    print(f"임베딩 및 DB 저장 시작 (배치 사이즈: {BATCH_SIZE})... 시간이 다소 소요될 수 있습니다.")
    
    for i in range(0, len(all_documents), BATCH_SIZE):
        batch_docs = all_documents[i:i + BATCH_SIZE]
        batch_meta = all_metadatas[i:i + BATCH_SIZE]
        batch_ids = all_ids[i:i + BATCH_SIZE]
        
        collection.upsert(
            documents=batch_docs,
            metadatas=batch_meta,
            ids=batch_ids
        )
        print(f"진행 상황: {min(i + BATCH_SIZE, len(all_documents))} / {len(all_documents)} 완료")
        
    print(f"--- 데이터 수집(Ingestion) 완료 ---")

if __name__ == "__main__":
    main()
