from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _as_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _as_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _env_or_secret(name: str, file_name: str) -> str:
    secret_path = os.getenv(file_name, "").strip()
    if secret_path:
        try:
            return Path(secret_path).read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return os.getenv(name, "").strip()


@dataclass(frozen=True)
class Settings:
    backend_host: str
    backend_port: int
    cors_origins: tuple[str, ...]
    request_timeout_seconds: int
    allow_local_fallback: bool
    chunk_size: int
    chunk_overlap: int
    ai_provider: str
    ai_base_url: str
    ai_model: str
    ai_api_key: str
    ai_max_tokens: int
    ai_temperature: float
    embedding_provider: str
    embedding_base_url: str
    embedding_model: str
    embedding_api_key: str
    vector_db_provider: str
    vector_db_path: Path
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str
    postgres_connect_timeout_seconds: int

    @classmethod
    def from_environment(cls) -> "Settings":
        nvidia_key = os.getenv("NVIDIA_API_KEY", "").strip()
        cors = tuple(
            item.strip()
            for item in os.getenv(
                "CORS_ORIGINS",
                "vscode-webview://*,http://127.0.0.1:*,http://localhost:*",
            ).split(",")
            if item.strip()
        )
        return cls(
            backend_host=os.getenv("BACKEND_HOST", "127.0.0.1").strip(),
            backend_port=_as_int("BACKEND_PORT", 8000),
            cors_origins=cors,
            request_timeout_seconds=_as_int("REQUEST_TIMEOUT_SECONDS", 60),
            allow_local_fallback=_as_bool("ALLOW_LOCAL_FALLBACK", True),
            chunk_size=max(200, _as_int("CHUNK_SIZE", 1600)),
            chunk_overlap=max(0, _as_int("CHUNK_OVERLAP", 200)),
            ai_provider=os.getenv("AI_PROVIDER", "nvidia").strip().lower(),
            ai_base_url=os.getenv(
                "AI_BASE_URL", "https://integrate.api.nvidia.com/v1"
            ).strip().rstrip("/"),
            ai_model=os.getenv(
                "AI_MODEL", "meta/llama-3.1-70b-instruct"
            ).strip(),
            ai_api_key=os.getenv("AI_API_KEY", nvidia_key).strip(),
            ai_max_tokens=max(32, _as_int("AI_MAX_TOKENS", 1024)),
            ai_temperature=min(2.0, max(0.0, _as_float("AI_TEMPERATURE", 0.2))),
            embedding_provider=os.getenv(
                "EMBEDDING_PROVIDER", "nvidia"
            ).strip().lower(),
            embedding_base_url=os.getenv(
                "EMBEDDING_BASE_URL", "https://integrate.api.nvidia.com/v1"
            ).strip().rstrip("/"),
            embedding_model=os.getenv(
                "EMBEDDING_MODEL", "nvidia/nv-embedqa-e5-v5"
            ).strip(),
            embedding_api_key=os.getenv("EMBEDDING_API_KEY", nvidia_key).strip(),
            vector_db_provider=os.getenv("VECTOR_DB_PROVIDER", "sqlite").strip().lower(),
            vector_db_path=_resolve_path(
                os.getenv("VECTOR_DB_PATH", "./data/vector_store.sqlite3")
            ),
            postgres_host=os.getenv("POSTGRES_HOST", "127.0.0.1").strip(),
            postgres_port=_as_int("POSTGRES_PORT", 5432),
            postgres_db=os.getenv("POSTGRES_DB", "vision").strip(),
            postgres_user=os.getenv("POSTGRES_USER", "vision").strip(),
            postgres_password=_env_or_secret(
                "POSTGRES_PASSWORD", "POSTGRES_PASSWORD_FILE"
            ),
            postgres_connect_timeout_seconds=max(
                1, _as_int("POSTGRES_CONNECT_TIMEOUT_SECONDS", 3)
            ),
        )

    def public_status(self) -> dict[str, object]:
        return {
            "ai_provider": self.ai_provider,
            "ai_model": self.ai_model,
            "ai_configured": bool(self.ai_api_key) or self.ai_provider == "local",
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "embedding_configured": bool(self.embedding_api_key)
            or self.embedding_provider == "local",
            "vector_db_provider": self.vector_db_provider,
            "metadata_db_provider": "postgresql",
            "metadata_db_configured": bool(self.postgres_password),
        }


settings = Settings.from_environment()
