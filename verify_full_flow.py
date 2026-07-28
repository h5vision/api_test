from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parent


def remove_database(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        target = Path(f"{path}{suffix}")
        for attempt in range(10):
            try:
                target.unlink(missing_ok=True)
                break
            except PermissionError:
                if attempt == 9:
                    print(f"warning: could not remove temporary file: {target}", file=sys.stderr)
                    break
                time.sleep(0.2)


def request_json(url: str, payload: dict | None = None, method: str = "GET") -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_until_ready(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.time() + 20
    while time.time() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise RuntimeError(f"Backend exited before startup:\n{output}")
        try:
            if request_json(f"{base_url}/v1/health").get("status") == "ok":
                return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.25)
    raise TimeoutError("Backend did not become ready within 20 seconds.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify ingest -> search -> AI chat flow")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Use NVIDIA API from .env instead of deterministic local providers",
    )
    args = parser.parse_args()

    port = 8011
    base_url = f"http://127.0.0.1:{port}"
    database_path = ROOT / "data" / "verification.sqlite3"
    remove_database(database_path)

    environment = os.environ.copy()
    environment.update(
        {
            "BACKEND_HOST": "127.0.0.1",
            "BACKEND_PORT": str(port),
            "VECTOR_DB_PROVIDER": "sqlite",
            "VECTOR_DB_PATH": str(database_path),
            "PYTHONIOENCODING": "utf-8",
        }
    )
    if args.live:
        environment["ALLOW_LOCAL_FALLBACK"] = "false"
    else:
        environment.update(
            {
                "AI_PROVIDER": "local",
                "EMBEDDING_PROVIDER": "local",
                "ALLOW_LOCAL_FALLBACK": "true",
            }
        )

    process = subprocess.Popen(
        [sys.executable, str(ROOT / "main.py")],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    try:
        wait_until_ready(base_url, process)
        ingest = request_json(
            f"{base_url}/v1/documents/ingest",
            {
                "project_id": "verification-project",
                "documents": [
                    {
                        "document_id": "payment.py",
                        "path": "src/payment.py",
                        "language": "python",
                        "text": (
                            "def retry_payment(order_id):\n"
                            "    # Payment failures are retried three times.\n"
                            "    return run_with_retry(order_id, max_attempts=3)\n"
                        ),
                    }
                ],
            },
            method="POST",
        )
        search = request_json(
            f"{base_url}/v1/search",
            {
                "project_id": "verification-project",
                "query": "결제 실패 재시도는 어디에서 처리하나요?",
                "top_k": 3,
            },
            method="POST",
        )
        chat = request_json(
            f"{base_url}/v1/chat",
            {
                "project_id": "verification-project",
                "message": "결제 실패 재시도 횟수와 함수를 알려줘",
                "session_id": "verification-project",
                "top_k": 3,
                "history": [],
            },
            method="POST",
        )

        assert ingest["chunks_stored"] >= 1
        assert search["results"] and search["results"][0]["document_id"] == "payment.py"
        assert chat["answer"] and chat["source"]
        assert chat["metadata"]["session_id"] == "verification-project"
        assert chat["metadata"]["project_id"] == "verification-project"
        print(
            json.dumps(
                {
                    "status": "ok",
                    "mode": "nvidia-live" if args.live else "local",
                    "chunks_stored": ingest["chunks_stored"],
                    "search_results": len(search["results"]),
                    "chat_sources": len(chat["source"]),
                    "providers": chat["metadata"],
                },
                ensure_ascii=False,
            )
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        remove_database(database_path)


if __name__ == "__main__":
    main()
