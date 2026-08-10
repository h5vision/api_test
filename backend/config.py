from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# P2-B.5: production configuration is injected by the deployment platform.
# Vision deliberately does not load a project-local .env file.


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


def _project_id_aliases() -> tuple[tuple[str, str], ...]:
    from .project_resolution import parse_project_id_aliases

    return parse_project_id_aliases(os.getenv("PROJECT_ID_ALIASES", ""))


@dataclass(frozen=True)
class Settings:
    backend_host: str
    backend_port: int
    frontend_host: str
    frontend_port: int
    admin_proxy_ip: str
    trusted_proxy_hosts: tuple[str, ...]
    cors_origins: tuple[str, ...]
    request_timeout_seconds: int
    allow_local_fallback: bool
    chunk_size: int
    chunk_overlap: int
    rag_candidate_k: int
    rag_min_sources: int
    rag_max_sources: int
    rag_context_max_chars: int
    rag_min_score: float
    rag_score_window: float
    agentic_rag_default_mode: str
    agentic_rag_balanced_steps: int
    agentic_rag_deep_steps: int
    agentic_rag_min_coverage: float
    agentic_rag_min_novelty_ratio: float
    project_id_aliases: tuple[tuple[str, str], ...]
    ai_provider: str
    ai_base_url: str
    ai_model: str
    ai_api_key: str
    ai_max_tokens: int
    ai_temperature: float
    ai_context_window_tokens: int
    ai_question_max_chars: int
    ai_history_max_chars: int
    ai_frontend_context_max_chars: int
    embedding_profile_id: str
    embedding_deployment: str
    embedding_provider: str
    embedding_base_url: str
    embedding_model: str
    embedding_model_id: str
    embedding_api_key: str
    embedding_dimension: int
    embedding_batch_size: int
    embedding_keep_alive: str
    embedding_timeout_seconds: int
    vector_db_provider: str
    vector_target_id: str
    qdrant_url: str
    qdrant_api_key: str
    qdrant_collection: str
    index_version: str
    rag_lab_base_url: str
    rag_lab_token: str
    rag_lab_timeout_seconds: int
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str
    postgres_connect_timeout_seconds: int
    redis_host: str
    redis_port: int
    redis_password: str
    redis_db: int
    task_queue_name: str
    task_consumer_group: str
    instance_id: str
    backendai_base_url: str
    backendai_api_key: str
    backendai_model: str
    backendai_public_model_id: str
    ai_provider_master_key: str
    nvidia_public_model_id: str
    groq_base_url: str
    groq_api_key: str
    groq_model: str
    groq_public_model_id: str
    default_model_id: str
    allow_cloud_fallback: bool
    project_db_local_root: Path
    offline_embedding_root: Path
    upload_root: Path
    upload_part_size_bytes: int
    upload_session_ttl_hours: int
    max_indexable_file_bytes: int
    snapshot_tenant_id: str
    snapshot_allowed_repositories: frozenset[str]
    snapshot_github_token: str
    snapshot_github_timeout_seconds: int

    @classmethod
    def from_environment(cls) -> "Settings":
        nvidia_key = os.getenv("NVIDIA_API_KEY", "").strip()
        # The canonical spelling is GROQ_API_KEY. Keep the legacy mixed-case
        # spelling compatible because Linux container environment names are
        # case-sensitive.
        groq_key = os.getenv(
            "GROQ_API_KEY", os.getenv("groq_API_KEY", "")
        ).strip()
        cors = tuple(
            item.strip()
            for item in os.getenv(
                "CORS_ORIGINS",
                "vscode-webview://*,http://127.0.0.1:*,http://localhost:*",
            ).split(",")
            if item.strip()
        )
        trusted_proxy_hosts = tuple(
            item.strip()
            for item in os.getenv(
                "TRUSTED_PROXY_HOSTS",
                "127.0.0.1/32,::1/128",
            ).split(",")
            if item.strip()
        )
        # Administrator-owned runtime routing is intentionally NOT sourced from
        # environment variables. These neutral values are never an operational
        # fallback; RuntimeSettingsResolver overlays persisted Admin settings and
        # request guards expose SETUP_REQUIRED until those rows exist.
        embedding_base_url = ""
        embedding_model = ""
        rag_candidate_k = max(4, min(50, _as_int("RAG_CANDIDATE_K", 12)))
        rag_max_sources = max(
            1,
            min(rag_candidate_k, _as_int("RAG_MAX_SOURCES", 8)),
        )
        rag_min_sources = max(
            1,
            min(rag_max_sources, _as_int("RAG_MIN_SOURCES", 3)),
        )
        return cls(
            backend_host=os.getenv("BACKEND_HOST", "127.0.0.1").strip(),
            backend_port=_as_int("BACKEND_PORT", 8000),
            frontend_host=os.getenv("FRONTEND_HOST", "127.0.0.1").strip(),
            frontend_port=max(1, min(65535, _as_int("FRONTEND_PORT", 8888))),
            admin_proxy_ip=os.getenv("ADMIN_PROXY_IP", "172.30.0.10").strip(),
            trusted_proxy_hosts=trusted_proxy_hosts,
            cors_origins=cors,
            request_timeout_seconds=_as_int("REQUEST_TIMEOUT_SECONDS", 60),
            allow_local_fallback=_as_bool("ALLOW_LOCAL_FALLBACK", False),
            chunk_size=max(200, _as_int("CHUNK_SIZE", 1600)),
            chunk_overlap=max(0, _as_int("CHUNK_OVERLAP", 200)),
            rag_candidate_k=rag_candidate_k,
            rag_min_sources=rag_min_sources,
            rag_max_sources=rag_max_sources,
            rag_context_max_chars=max(
                2_000,
                _as_int("RAG_CONTEXT_MAX_CHARS", 12_000),
            ),
            rag_min_score=min(
                1.0,
                max(-1.0, _as_float("RAG_MIN_SCORE", 0.30)),
            ),
            rag_score_window=min(
                1.0,
                max(0.0, _as_float("RAG_SCORE_WINDOW", 0.20)),
            ),
            agentic_rag_default_mode=(
                os.getenv("AGENTIC_RAG_DEFAULT_MODE", "auto").strip().lower()
                if os.getenv("AGENTIC_RAG_DEFAULT_MODE", "auto").strip().lower()
                in {"auto", "fast", "balanced", "deep"}
                else "auto"
            ),
            agentic_rag_balanced_steps=max(
                1,
                min(4, _as_int("AGENTIC_RAG_BALANCED_STEPS", 2)),
            ),
            agentic_rag_deep_steps=max(
                2,
                min(6, _as_int("AGENTIC_RAG_DEEP_STEPS", 3)),
            ),
            agentic_rag_min_coverage=min(
                1.0,
                max(0.0, _as_float("AGENTIC_RAG_MIN_COVERAGE", 0.55)),
            ),
            agentic_rag_min_novelty_ratio=min(
                1.0,
                max(0.0, _as_float("AGENTIC_RAG_MIN_NOVELTY_RATIO", 0.10)),
            ),
            project_id_aliases=_project_id_aliases(),
            ai_provider="unconfigured",
            ai_base_url="",
            ai_model="",
            ai_api_key=os.getenv("AI_API_KEY", nvidia_key).strip(),
            ai_max_tokens=max(32, _as_int("AI_MAX_TOKENS", 1024)),
            ai_temperature=min(2.0, max(0.0, _as_float("AI_TEMPERATURE", 0.2))),
            ai_context_window_tokens=max(
                4_096,
                min(131_072, _as_int("AI_CONTEXT_WINDOW_TOKENS", 32_768)),
            ),
            ai_question_max_chars=max(
                2_000,
                _as_int("AI_QUESTION_MAX_CHARS", 20_000),
            ),
            ai_history_max_chars=max(
                2_000,
                _as_int("AI_HISTORY_MAX_CHARS", 12_000),
            ),
            ai_frontend_context_max_chars=max(
                2_000,
                _as_int("AI_FRONTEND_CONTEXT_MAX_CHARS", 24_000),
            ),
            embedding_profile_id="",
            embedding_deployment="api",
            embedding_provider="unconfigured",
            embedding_base_url=embedding_base_url,
            embedding_model=embedding_model,
            embedding_model_id="",
            embedding_api_key=os.getenv("EMBEDDING_API_KEY", nvidia_key).strip(),
            embedding_dimension=0,
            embedding_batch_size=1,
            embedding_keep_alive=os.getenv("EMBEDDING_KEEP_ALIVE", "10m").strip(),
            embedding_timeout_seconds=max(
                30, _as_int("EMBEDDING_TIMEOUT_SECONDS", 300)
            ),
            vector_db_provider="qdrant",
            vector_target_id="",
            qdrant_url="",
            qdrant_api_key=_env_or_secret("QDRANT_API_KEY", "QDRANT_API_KEY_FILE"),
            qdrant_collection="",
            index_version="",
            rag_lab_base_url=os.getenv("RAG_LAB_BASE_URL", "").strip().rstrip("/"),
            rag_lab_token=_env_or_secret("RAG_LAB_TOKEN", "RAG_LAB_TOKEN_FILE"),
            rag_lab_timeout_seconds=max(
                1, _as_int("RAG_LAB_TIMEOUT_SECONDS", 60)
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
            redis_host=os.getenv("REDIS_HOST", "127.0.0.1").strip(),
            redis_port=max(1, min(65535, _as_int("REDIS_PORT", 6379))),
            redis_password=_env_or_secret(
                "REDIS_PASSWORD", "REDIS_PASSWORD_FILE"
            ),
            redis_db=max(0, _as_int("REDIS_DB", 0)),
            task_queue_name=os.getenv(
                "TASK_QUEUE_NAME", "vision:tasks:indexing"
            ).strip(),
            task_consumer_group=os.getenv("TASK_CONSUMER_GROUP", "").strip(),
            instance_id=os.getenv(
                "INSTANCE_ID",
                os.getenv("HOSTNAME", "vision-local"),
            ).strip(),
            backendai_base_url="",
            backendai_api_key=_env_or_secret(
                "BACKENDAI_API_KEY", "BACKENDAI_API_KEY_FILE"
            ),
            backendai_model="",
            backendai_public_model_id=os.getenv(
                "BACKENDAI_PUBLIC_MODEL_ID", "backendai-default"
            ).strip(),
            ai_provider_master_key=_env_or_secret(
                "AI_PROVIDER_MASTER_KEY", "AI_PROVIDER_MASTER_KEY_FILE"
            ),
            nvidia_public_model_id=os.getenv(
                "NVIDIA_PUBLIC_MODEL_ID", "nvidia-default"
            ).strip(),
            groq_base_url="",
            groq_api_key=groq_key,
            groq_model="",
            groq_public_model_id=os.getenv(
                "GROQ_PUBLIC_MODEL_ID", "groq-default"
            ).strip(),
            default_model_id="",
            allow_cloud_fallback=False,
            project_db_local_root=_resolve_path(
                os.getenv(
                    "PROJECT_DB_LOCAL_ROOT",
                    "./projects",
                )
            ),
            offline_embedding_root=_resolve_path(
                os.getenv("OFFLINE_EMBEDDING_ROOT", "./embedding-results")
            ),
            upload_root=_resolve_path(os.getenv("UPLOAD_ROOT", "./data/uploads")),
            upload_part_size_bytes=max(
                1024 * 1024, _as_int("UPLOAD_PART_SIZE_BYTES", 16 * 1024 * 1024)
            ),
            upload_session_ttl_hours=max(
                1, _as_int("UPLOAD_SESSION_TTL_HOURS", 72)
            ),
            max_indexable_file_bytes=max(
                1024, _as_int("MAX_INDEXABLE_FILE_BYTES", 16 * 1024 * 1024)
            ),
            snapshot_tenant_id=(
                os.getenv("SNAPSHOT_TENANT_ID", "vision-default").strip()
                or "vision-default"
            ),
            snapshot_allowed_repositories=frozenset(
                item.strip().casefold()
                for item in os.getenv(
                    "SNAPSHOT_ALLOWED_REPOSITORIES", "h5vision/api_test"
                ).split(",")
                if item.strip()
            ),
            snapshot_github_token=_env_or_secret(
                "SNAPSHOT_GITHUB_TOKEN", "SNAPSHOT_GITHUB_TOKEN_FILE"
            ),
            snapshot_github_timeout_seconds=max(
                1, _as_int("SNAPSHOT_GITHUB_TIMEOUT_SECONDS", 20)
            ),
        )

    def public_status(self) -> dict[str, object]:
        return {
            "ai_provider": self.ai_provider,
            "ai_model": self.ai_model,
            "ai_context_window_tokens": self.ai_context_window_tokens,
            "ai_configured": bool(self.ai_api_key) or self.ai_provider == "local",
            "embedding_profile_id": self.embedding_profile_id,
            "embedding_deployment": self.embedding_deployment,
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "embedding_model_id": self.embedding_model_id,
            "embedding_configured": bool(self.embedding_api_key)
            or self.embedding_provider in {"local", "ollama"},
            "embedding_dimension": self.embedding_dimension,
            "vector_db_provider": self.vector_db_provider,
            "vector_target_id": self.vector_target_id,
            "vector_collection": self.qdrant_collection,
            "index_version": self.index_version,
            "external_vector_prompt_configured": bool(self.rag_lab_base_url),
            "external_vector_prompt_base_url": self.rag_lab_base_url,
            "agentic_rag_default_mode": self.agentic_rag_default_mode,
            "metadata_db_provider": "postgresql",
            "metadata_db_configured": bool(self.postgres_password),
            "default_model_id": self.default_model_id,
            "backendai_base_url_configured": bool(self.backendai_base_url),
            "groq_configured": bool(self.groq_api_key),
            "snapshot_tenant_id": self.snapshot_tenant_id,
            "snapshot_github_auth_configured": bool(self.snapshot_github_token),
        }


settings = Settings.from_environment()
