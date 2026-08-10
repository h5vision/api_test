from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4

import psycopg
from cryptography.fernet import Fernet, InvalidToken
from psycopg.rows import dict_row

from .config import Settings
from .schema_guard import SchemaStateError, require_schema
from .schemas import ModelInfo
from .services import ServiceError


_EMBEDDING_MODEL_PATTERN = re.compile(
    r"(?:embed(?:ding)?|(?:^|[/_.:-])(?:bge|e5|gte)(?:$|[/_.:-])|"
    r"feature[-_:]?extraction|retrieval[-_:]?encoder)",
    re.IGNORECASE,
)
_NON_CHAT_MODEL_PATTERN = re.compile(
    r"(?:rerank|rankqa|reward|whisper|(?:^|[/_.:-])tts(?:$|[/_.:-])|"
    r"speech|audio|guard|moderation|ocr)",
    re.IGNORECASE,
)


class AIProviderStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class AIProvider:
    provider_id: str
    name: str
    protocol: str
    base_url: str
    auth_type: str
    api_key: str | None
    api_key_configured: bool
    api_key_hint: str | None
    enabled: bool
    deployment_type: str
    chat_processing_mode: str
    status: str
    error: str | None
    latency_ms: int
    model_count: int
    last_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class DiscoveredProviderModel:
    provider_id: str
    provider_name: str
    protocol: str
    base_url: str
    deployment_type: str
    provider_enabled: bool
    provider_status: str
    model_name: str
    discovered_at: datetime


@dataclass(frozen=True)
class DiscoveryResult:
    status: str
    models: list[str]
    latency_ms: int
    error: str | None = None
    skipped_models: list[str] = field(default_factory=list)


class PostgresAIProviderStore:
    """CRUD storage for AI providers with encrypted-at-rest API credentials."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._initialized = False
        self._initialize_lock = threading.Lock()
        self._cipher: Fernet | None = None
        if settings.ai_provider_master_key:
            try:
                self._cipher = Fernet(settings.ai_provider_master_key.encode("ascii"))
            except (ValueError, TypeError) as exc:
                raise AIProviderStoreError(
                    "AI_PROVIDER_MASTER_KEY must be a valid Fernet key"
                ) from exc

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(
            host=self._settings.postgres_host,
            port=self._settings.postgres_port,
            dbname=self._settings.postgres_db,
            user=self._settings.postgres_user,
            password=self._settings.postgres_password,
            connect_timeout=self._settings.postgres_connect_timeout_seconds,
            row_factory=dict_row,
        )

    def _ensure_schema(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            try:
                with self._connect() as connection:
                    require_schema(connection)
                self._initialized = True
            except (psycopg.Error, OSError, SchemaStateError) as exc:
                raise AIProviderStoreError(
                    "PostgreSQL schema is not on the required Alembic baseline"
                ) from exc

    def _encrypt(self, api_key: str) -> str:
        if self._cipher is None:
            raise AIProviderStoreError(
                "API keys require AI_PROVIDER_MASTER_KEY or "
                "AI_PROVIDER_MASTER_KEY_FILE"
            )
        return self._cipher.encrypt(api_key.encode("utf-8")).decode("ascii")

    def _decrypt(self, ciphertext: str | None) -> str | None:
        if not ciphertext:
            return None
        if self._cipher is None:
            raise AIProviderStoreError(
                "Stored API key cannot be decrypted without the master key"
            )
        try:
            return self._cipher.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
            raise AIProviderStoreError("Stored AI provider API key is invalid") from exc

    def _record(self, row: dict[str, Any], *, with_secret: bool) -> AIProvider:
        ciphertext = row.get("api_key_ciphertext")
        return AIProvider(
            provider_id=str(row["provider_id"]),
            name=str(row["name"]),
            protocol=str(row["protocol"]),
            base_url=str(row["base_url"]),
            auth_type=str(row["auth_type"]),
            api_key=self._decrypt(ciphertext) if with_secret else None,
            api_key_configured=bool(ciphertext),
            api_key_hint=(
                str(row["api_key_hint"]) if row.get("api_key_hint") else None
            ),
            enabled=bool(row["enabled"]),
            deployment_type=str(row["deployment_type"]),
            chat_processing_mode=str(row.get("chat_processing_mode") or "vision_managed"),
            status=str(row["status"]),
            error=str(row["error"]) if row.get("error") else None,
            latency_ms=int(row["latency_ms"]),
            model_count=int(row["model_count"]),
            last_checked_at=row.get("last_checked_at"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _returning_sql() -> str:
        return """
            RETURNING provider_id, name, protocol, base_url, auth_type,
                      api_key_ciphertext, api_key_hint, enabled,
                      deployment_type, chat_processing_mode, status, error, latency_ms, model_count,
                      last_checked_at, created_at, updated_at
        """

    def list(self, *, with_secrets: bool = False) -> list[AIProvider]:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT provider_id, name, protocol, base_url, auth_type,
                           api_key_ciphertext, api_key_hint, enabled,
                           deployment_type, chat_processing_mode, status, error, latency_ms, model_count,
                           last_checked_at, created_at, updated_at
                    FROM ai_provider_configs
                    ORDER BY created_at, provider_id
                    """
                ).fetchall()
            return [self._record(row, with_secret=with_secrets) for row in rows]
        except (psycopg.Error, OSError) as exc:
            raise AIProviderStoreError("AI provider list failed") from exc

    def get(
        self,
        provider_id: str,
        *,
        with_secret: bool = False,
    ) -> AIProvider | None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT provider_id, name, protocol, base_url, auth_type,
                           api_key_ciphertext, api_key_hint, enabled,
                           deployment_type, chat_processing_mode, status, error, latency_ms, model_count,
                           last_checked_at, created_at, updated_at
                    FROM ai_provider_configs
                    WHERE provider_id = %s
                    """,
                    (provider_id,),
                ).fetchone()
            return self._record(row, with_secret=with_secret) if row else None
        except (psycopg.Error, OSError) as exc:
            raise AIProviderStoreError("AI provider read failed") from exc

    def create(
        self,
        *,
        name: str,
        protocol: str,
        base_url: str,
        auth_type: str,
        api_key: str | None,
        enabled: bool,
        deployment_type: str,
        chat_processing_mode: str = "vision_managed",
    ) -> AIProvider:
        self._ensure_schema()
        provider_id = f"aip_{uuid4().hex}"
        ciphertext = self._encrypt(api_key) if api_key else None
        key_hint = f"••••{api_key[-4:]}" if api_key else None
        try:
            with self._connect() as connection:
                row = connection.execute(
                    f"""
                    INSERT INTO ai_provider_configs (
                        provider_id, name, protocol, base_url, auth_type,
                        api_key_ciphertext, api_key_hint, enabled, deployment_type,
                        chat_processing_mode
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    {self._returning_sql()}
                    """,
                    (
                        provider_id,
                        name,
                        protocol,
                        base_url,
                        auth_type,
                        ciphertext,
                        key_hint,
                        enabled,
                        deployment_type,
                        chat_processing_mode,
                    ),
                ).fetchone()
            if row is None:
                raise AIProviderStoreError("AI provider insert returned no row")
            return self._record(row, with_secret=False)
        except (psycopg.Error, OSError) as exc:
            raise AIProviderStoreError("AI provider create failed") from exc

    def update(
        self,
        provider_id: str,
        *,
        name: str,
        protocol: str,
        base_url: str,
        auth_type: str,
        api_key: str | None,
        clear_api_key: bool,
        enabled: bool,
        deployment_type: str,
        chat_processing_mode: str = "vision_managed",
    ) -> AIProvider | None:
        self._ensure_schema()
        current = self.get(provider_id)
        if current is None:
            return None
        ciphertext: str | None
        key_hint: str | None
        if clear_api_key:
            ciphertext = None
            key_hint = None
        elif api_key:
            ciphertext = self._encrypt(api_key)
            key_hint = f"••••{api_key[-4:]}"
        else:
            try:
                with self._connect() as connection:
                    secret_row = connection.execute(
                        """
                        SELECT api_key_ciphertext, api_key_hint
                        FROM ai_provider_configs
                        WHERE provider_id = %s
                        """,
                        (provider_id,),
                    ).fetchone()
            except (psycopg.Error, OSError) as exc:
                raise AIProviderStoreError("AI provider secret read failed") from exc
            ciphertext = secret_row["api_key_ciphertext"] if secret_row else None
            key_hint = secret_row["api_key_hint"] if secret_row else None
        try:
            with self._connect() as connection:
                row = connection.execute(
                    f"""
                    UPDATE ai_provider_configs
                    SET name = %s,
                        protocol = %s,
                        base_url = %s,
                        auth_type = %s,
                        api_key_ciphertext = %s,
                        api_key_hint = %s,
                        enabled = %s,
                        deployment_type = %s,
                        chat_processing_mode = %s,
                        status = 'unknown',
                        error = NULL,
                        model_count = 0,
                        last_checked_at = NULL,
                        updated_at = NOW()
                    WHERE provider_id = %s
                    {self._returning_sql()}
                    """,
                    (
                        name,
                        protocol,
                        base_url,
                        auth_type,
                        ciphertext,
                        key_hint,
                        enabled,
                        deployment_type,
                        chat_processing_mode,
                        provider_id,
                    ),
                ).fetchone()
                connection.execute(
                    "DELETE FROM ai_provider_models WHERE provider_id = %s",
                    (provider_id,),
                )
            return self._record(row, with_secret=False) if row else None
        except (psycopg.Error, OSError) as exc:
            raise AIProviderStoreError("AI provider update failed") from exc

    def delete(self, provider_id: str) -> bool:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM ai_provider_configs WHERE provider_id = %s",
                    (provider_id,),
                )
            return bool(cursor.rowcount)
        except (psycopg.Error, OSError) as exc:
            raise AIProviderStoreError("AI provider delete failed") from exc

    def update_discovery(
        self,
        provider_id: str,
        result: DiscoveryResult,
    ) -> AIProvider:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                connection.execute(
                    "DELETE FROM ai_provider_models WHERE provider_id = %s",
                    (provider_id,),
                )
                if result.models:
                    with connection.cursor() as cursor:
                        cursor.executemany(
                            """
                            INSERT INTO ai_provider_models (
                                provider_id, model_name, discovered_at
                            ) VALUES (%s, %s, NOW())
                            ON CONFLICT (provider_id, model_name)
                            DO UPDATE SET discovered_at = NOW()
                            """,
                            [(provider_id, model) for model in result.models],
                        )
                row = connection.execute(
                    f"""
                    UPDATE ai_provider_configs
                    SET status = %s,
                        error = %s,
                        latency_ms = %s,
                        model_count = %s,
                        last_checked_at = NOW(),
                        updated_at = NOW()
                    WHERE provider_id = %s
                    {self._returning_sql()}
                    """,
                    (
                        result.status,
                        result.error,
                        max(0, result.latency_ms),
                        len(result.models),
                        provider_id,
                    ),
                ).fetchone()
            if row is None:
                raise AIProviderStoreError("AI provider was not found")
            return self._record(row, with_secret=False)
        except (psycopg.Error, OSError) as exc:
            raise AIProviderStoreError("AI provider discovery update failed") from exc

    def discovered_models(self) -> list[DiscoveredProviderModel]:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT p.provider_id, p.name AS provider_name, p.protocol,
                           p.base_url, p.deployment_type,
                           p.enabled AS provider_enabled,
                           p.status AS provider_status,
                           m.model_name, m.discovered_at
                    FROM ai_provider_models AS m
                    JOIN ai_provider_configs AS p
                      ON p.provider_id = m.provider_id
                    ORDER BY p.created_at, m.model_name
                    """
                ).fetchall()
            return [DiscoveredProviderModel(**row) for row in rows]
        except (psycopg.Error, OSError) as exc:
            raise AIProviderStoreError("AI provider model list failed") from exc


class AIProviderRegistry:
    def __init__(
        self,
        store: PostgresAIProviderStore,
        settings: Settings,
        model_enabled_provider: Callable[[str], bool] | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self._model_enabled_provider = model_enabled_provider
        self._refresh_lock = threading.Lock()

    @staticmethod
    def model_id(provider_id: str, model_name: str) -> str:
        encoded = urllib.parse.quote(model_name, safe="")
        return f"provider:{provider_id}:{encoded}"

    @staticmethod
    def parse_model_id(model_id: str) -> tuple[str, str] | None:
        if not model_id.startswith("provider:"):
            return None
        parts = model_id.split(":", 2)
        if len(parts) != 3 or not parts[1] or not parts[2]:
            return None
        return parts[1], urllib.parse.unquote(parts[2])

    @staticmethod
    def _auth_headers(auth_type: str, api_key: str | None) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "VisionBackend/1.0",
        }
        if auth_type == "bearer" and api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        elif auth_type == "x-api-key" and api_key:
            headers["X-API-Key"] = api_key
        return headers

    @classmethod
    def _headers(cls, provider: AIProvider) -> dict[str, str]:
        return cls._auth_headers(provider.auth_type, provider.api_key)

    @staticmethod
    def _looks_like_embedding_model(model_name: str) -> bool:
        return bool(_EMBEDDING_MODEL_PATTERN.search(model_name))

    @classmethod
    def _catalog_capabilities(
        cls,
        item: dict[str, Any],
        model_name: str,
    ) -> tuple[bool, bool]:
        tokens: set[str] = set()
        for key in ("capabilities", "capability", "tasks", "task"):
            value = item.get(key)
            if isinstance(value, dict):
                tokens.update(
                    str(name).casefold()
                    for name, enabled in value.items()
                    if enabled
                )
            elif isinstance(value, list):
                tokens.update(str(entry).casefold() for entry in value)
            elif isinstance(value, str):
                tokens.update(
                    part.casefold()
                    for part in re.split(r"[,\s]+", value)
                    if part
                )
        for key in ("type", "object", "pipeline_tag", "model_type"):
            value = item.get(key)
            if isinstance(value, str):
                tokens.add(value.casefold())
        embedding = cls._looks_like_embedding_model(model_name) or any(
            "embed" in token or token in {"feature-extraction", "retrieval"}
            for token in tokens
        )
        explicit_chat = any(
            token
            in {
                "completion",
                "chat",
                "chat.completions",
                "generation",
                "text-generation",
                "conversational",
                "tools",
                "vision",
            }
            or "completion" in token
            for token in tokens
        )
        chat = explicit_chat or (
            not embedding and not _NON_CHAT_MODEL_PATTERN.search(model_name)
        )
        return chat, embedding

    @classmethod
    def detect_protocol(
        cls,
        base_url: str,
        auth_type: str,
        api_key: str | None,
    ) -> str:
        """Detect Ollama first, then an OpenAI-compatible model catalog."""

        headers = cls._auth_headers(auth_type, api_key)
        probes = (
            ("ollama", "/api/tags", "models"),
            ("openai", "/models", "data"),
        )
        for protocol, path, catalog_key in probes:
            request = urllib.request.Request(
                f"{base_url}{path}",
                headers=headers,
                method="GET",
            )
            try:
                with urllib.request.urlopen(request, timeout=3) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if isinstance(payload, dict) and isinstance(
                    payload.get(catalog_key), list
                ):
                    return protocol
            except (
                urllib.error.HTTPError,
                urllib.error.URLError,
                TimeoutError,
                ValueError,
            ):
                continue
        parsed = urllib.parse.urlparse(base_url)
        return "ollama" if parsed.port == 11434 else "openai"

    @classmethod
    def probe_ollama(
        cls,
        base_url: str,
        *,
        auth_type: str = "none",
        api_key: str | None = None,
        timeout_seconds: int = 3,
    ) -> DiscoveryResult:
        """Return only models that Ollama reports as completion-capable."""

        started_at = perf_counter()
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/api/tags",
            headers=cls._auth_headers(auth_type, api_key),
            method="GET",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout_seconds,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
            entries = payload.get("models", [])
            if not isinstance(entries, list):
                raise ValueError("models is not a list")
            models: set[str] = set()
            skipped: set[str] = set()
            for item in entries:
                if not isinstance(item, dict):
                    continue
                model_name = str(
                    item.get("name") or item.get("model") or ""
                ).strip()
                if not model_name:
                    continue
                capabilities = item.get("capabilities")
                if (
                    isinstance(capabilities, list)
                    and capabilities
                    and "completion" not in {
                        str(capability).strip().lower()
                        for capability in capabilities
                    }
                ):
                    skipped.add(model_name)
                    continue
                models.add(model_name)
            normalized = sorted(models)
            skipped_models = sorted(skipped)
            return DiscoveryResult(
                "online" if normalized else "degraded",
                normalized,
                round((perf_counter() - started_at) * 1000),
                (
                    None
                    if normalized
                    else "no_chat_models"
                    if skipped_models
                    else "no_models"
                ),
                skipped_models,
            )
        except urllib.error.HTTPError as exc:
            return DiscoveryResult(
                "offline",
                [],
                round((perf_counter() - started_at) * 1000),
                f"http_{exc.code}",
            )
        except (
            urllib.error.URLError,
            TimeoutError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            reason = "timeout" if isinstance(exc, TimeoutError) else "unreachable"
            if isinstance(exc, urllib.error.URLError) and isinstance(
                exc.reason, TimeoutError
            ):
                reason = "timeout"
            return DiscoveryResult(
                "offline",
                [],
                round((perf_counter() - started_at) * 1000),
                reason,
            )

    def discover(self, provider_id: str) -> AIProvider:
        provider = self.store.get(provider_id, with_secret=True)
        if provider is None:
            raise AIProviderStoreError("AI provider was not found")
        if not provider.enabled:
            return self.store.update_discovery(
                provider_id,
                DiscoveryResult("disabled", [], 0, "disabled"),
            )
        if provider.protocol == "ollama":
            result = self.probe_ollama(
                provider.base_url,
                auth_type=provider.auth_type,
                api_key=provider.api_key,
                timeout_seconds=5,
            )
            return self.store.update_discovery(provider_id, result)
        path = "/models"
        request = urllib.request.Request(
            f"{provider.base_url}{path}",
            headers=self._headers(provider),
            method="GET",
        )
        started_at = perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                entries = payload.get("data", [])
                models = {
                    str(item.get("id") or "").strip()
                    for item in entries
                    if isinstance(item, dict)
                }
                normalized = sorted(model for model in models if model)
                result = DiscoveryResult(
                    "online" if normalized else "degraded",
                    normalized,
                    round((perf_counter() - started_at) * 1000),
                    None if normalized else "no_models",
                )
        except urllib.error.HTTPError as exc:
            result = DiscoveryResult(
                "offline",
                [],
                round((perf_counter() - started_at) * 1000),
                f"http_{exc.code}",
            )
        except (
            urllib.error.URLError,
            TimeoutError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            reason = "timeout" if isinstance(exc, TimeoutError) else "unreachable"
            if isinstance(exc, urllib.error.URLError) and isinstance(
                exc.reason, TimeoutError
            ):
                reason = "timeout"
            result = DiscoveryResult(
                "offline",
                [],
                round((perf_counter() - started_at) * 1000),
                reason,
            )
        return self.store.update_discovery(provider_id, result)

    def refresh_stale(self, *, max_age_seconds: int = 30) -> None:
        threshold = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
        with self._refresh_lock:
            for provider in self.store.list():
                if (
                    provider.enabled
                    and (
                        provider.last_checked_at is None
                        or provider.last_checked_at < threshold
                    )
                ):
                    try:
                        self.discover(provider.provider_id)
                    except AIProviderStoreError:
                        continue

    def models(self, default_model_id: str) -> list[ModelInfo]:
        self.refresh_stale()
        result: list[ModelInfo] = []
        for item in self.store.discovered_models():
            model_id = self.model_id(item.provider_id, item.model_name)
            enabled = (
                item.provider_enabled
                and (
                    self._model_enabled_provider(model_id)
                    if self._model_enabled_provider is not None
                    else True
                )
            )
            result.append(
                ModelInfo(
                    model_id=model_id,
                    model_name=item.model_name,
                    display_name=f"{item.provider_name} ({item.model_name})",
                    provider="custom",
                    location=(
                        "cloud"
                        if item.deployment_type == "cloud"
                        else "local"
                        if item.deployment_type == "local"
                        else "internal"
                    ),
                    deployment_type=item.deployment_type,
                    endpoint=self._endpoint_label(item.base_url),
                    enabled=enabled,
                    available=(
                        item.provider_status == "online"
                        and item.provider_enabled
                    ),
                    is_default=default_model_id == model_id,
                    streaming=False,
                )
            )
        return result

    @staticmethod
    def _endpoint_label(base_url: str) -> str | None:
        parsed = urllib.parse.urlparse(base_url)
        if not parsed.hostname:
            return None
        return (
            f"{parsed.hostname}:{parsed.port}"
            if parsed.port is not None
            else parsed.hostname
        )

    @staticmethod
    def _extract_answer(payload: dict[str, Any]) -> str:
        message = payload.get("message")
        if isinstance(message, dict):
            answer = message.get("content")
            if isinstance(answer, str) and answer.strip():
                return answer.strip()
        try:
            answer = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ServiceError("AI Provider 응답에 answer가 없습니다.") from exc
        if not isinstance(answer, str) or not answer.strip():
            raise ServiceError("AI Provider가 빈 답변을 반환했습니다.")
        return answer.strip()

    def chat_processing_mode(self, model_id: str) -> str:
        parsed = self.parse_model_id(model_id)
        if parsed is None:
            return "vision_managed"
        provider_id, _model_name = parsed
        provider = self.store.get(provider_id)
        if provider is None or not provider.enabled:
            return "vision_managed"
        return provider.chat_processing_mode

    def generate(
        self,
        model_id: str,
        messages: list[dict[str, str]],
        *,
        routing_metadata: dict[str, str | None] | None = None,
    ) -> tuple[str, str, str]:
        parsed = self.parse_model_id(model_id)
        if parsed is None:
            raise ServiceError(f"지원하지 않는 Provider model_id입니다: {model_id}", 422)
        provider_id, model_name = parsed
        provider = self.store.get(provider_id, with_secret=True)
        if provider is None or not provider.enabled:
            raise ServiceError("AI Provider가 없거나 비활성화되어 있습니다.", 503)
        known_models = {
            model.model_name
            for model in self.store.discovered_models()
            if model.provider_id == provider_id
        }
        if model_name not in known_models:
            self.discover(provider_id)
            known_models = {
                model.model_name
                for model in self.store.discovered_models()
                if model.provider_id == provider_id
            }
        if model_name not in known_models:
            raise ServiceError(
                f"AI Provider에서 모델을 찾을 수 없습니다: {model_name}",
                422,
            )
        headers = self._headers(provider)
        headers["Content-Type"] = "application/json"
        for key, value in (routing_metadata or {}).items():
            if value is not None and str(value):
                headers[f"X-Vision-{key.replace('_', '-').title()}"] = (
                    urllib.parse.quote(str(value), safe="._:/-")
                )
        if provider.protocol == "ollama":
            path = "/api/chat"
            body = {
                "model": model_name,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": self.settings.ai_temperature,
                    "num_predict": self.settings.ai_max_tokens,
                },
            }
        else:
            path = "/chat/completions"
            body = {
                "model": model_name,
                "messages": messages,
                "temperature": self.settings.ai_temperature,
                "max_tokens": self.settings.ai_max_tokens,
                "stream": False,
            }
        request = urllib.request.Request(
            f"{provider.base_url}{path}",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.settings.request_timeout_seconds,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ServiceError(
                f"AI Provider가 HTTP {exc.code}를 반환했습니다.",
                502 if exc.code >= 500 else 403 if exc.code in {401, 403} else 422,
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            status_code = 504 if isinstance(exc, TimeoutError) else 503
            raise ServiceError("AI Provider에 연결할 수 없습니다.", status_code) from exc
        except (ValueError, json.JSONDecodeError) as exc:
            raise ServiceError("AI Provider 응답이 올바른 JSON이 아닙니다.", 502) from exc
        return self._extract_answer(payload), provider.name, model_name
