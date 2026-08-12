from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class AIProviderWriteRequest(BaseModel):
    """Administrator-managed inference endpoint.

    ``base_url`` supports provider URLs such as ``https://api.groq.com/openai/v1``.
    For an Ollama server on a LAN, ``host`` and ``port`` can be supplied instead.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=100)
    protocol: Literal["auto", "ollama", "openai"] = "auto"
    base_url: str | None = Field(default=None, min_length=8, max_length=2048)
    host: str | None = Field(default=None, min_length=1, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    use_tls: bool = False
    auth_type: Literal["none", "bearer", "x-api-key"] = "none"
    api_key: str | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    enabled: bool = True
    deployment_type: Literal["cloud", "local", "remote_server"] = "remote_server"
    chat_processing_mode: Literal["vision_managed", "provider_managed"] = "vision_managed"

    @field_validator("name")
    @classmethod
    def normalize_provider_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized

    @field_validator("api_key")
    @classmethod
    def normalize_provider_api_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("base_url")
    @classmethod
    def validate_provider_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError(
                "base_url must be an absolute HTTP(S) URL without credentials or fragment"
            )
        return normalized

    @field_validator("host")
    @classmethod
    def validate_provider_host(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if (
            not normalized
            or "/" in normalized
            or "\\" in normalized
            or "@" in normalized
            or any(character.isspace() for character in normalized)
        ):
            raise ValueError("host must be an IP address or DNS name")
        return normalized

    @model_validator(mode="after")
    def validate_provider_connection(self) -> "AIProviderWriteRequest":
        if self.base_url is None and (self.host is None or self.port is None):
            raise ValueError("base_url or both host and port are required")
        if self.base_url is not None and (self.host is not None or self.port is not None):
            raise ValueError("use either base_url or host/port, not both")
        if self.auth_type == "none" and self.api_key:
            raise ValueError("api_key requires bearer or x-api-key auth_type")
        if self.clear_api_key and self.api_key:
            raise ValueError("api_key and clear_api_key cannot be used together")
        return self

    def resolved_base_url(self) -> str:
        if self.base_url:
            return self.base_url
        scheme = "https" if self.use_tls else "http"
        return f"{scheme}://{self.host}:{self.port}"
class AIProviderRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    name: str
    protocol: Literal["ollama", "openai"]
    base_url: str
    auth_type: Literal["none", "bearer", "x-api-key"]
    api_key_configured: bool
    api_key_hint: str | None = None
    enabled: bool
    deployment_type: Literal["cloud", "local", "remote_server"]
    chat_processing_mode: Literal["vision_managed", "provider_managed"] = "vision_managed"
    status: Literal["unknown", "online", "degraded", "offline", "disabled"]
    error: str | None = None
    latency_ms: int = Field(default=0, ge=0)
    model_count: int = Field(default=0, ge=0)
    models: list[str] = Field(default_factory=list)
    last_checked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
class AIProviderListResponse(BaseModel):
    providers: list[AIProviderRecord]
    total: int = Field(..., ge=0)
class CloudProviderCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_type: Literal["nvidia", "groq", "openai", "custom"]
    name: str | None = Field(default=None, max_length=100)
    api_key: str = Field(..., min_length=1, max_length=4096)
    base_url: str | None = Field(default=None, min_length=8, max_length=2048)
    auth_type: Literal["bearer", "x-api-key"] = "bearer"
    enabled: bool = True

    @field_validator("name", "api_key")
    @classmethod
    def normalize_cloud_credential_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("cloud credential field must not be blank")
        return normalized

    @field_validator("base_url")
    @classmethod
    def validate_cloud_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        return normalized

    @model_validator(mode="after")
    def validate_custom_cloud_provider(self) -> "CloudProviderCredentialRequest":
        if self.provider_type == "custom" and not self.base_url:
            raise ValueError("base_url is required for a custom provider")
        if self.provider_type != "custom" and self.auth_type != "bearer":
            raise ValueError("known Cloud providers use Bearer authentication")
        return self

__all__ = ['AIProviderWriteRequest', 'AIProviderRecord', 'AIProviderListResponse', 'CloudProviderCredentialRequest']
