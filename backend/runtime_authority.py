from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from time import monotonic
from typing import Any
from urllib.parse import unquote

from .config import Settings
from .embedding_profiles import EmbeddingProfileStoreError, PostgresEmbeddingProfileStore
from .runtime_config import PostgresRuntimeNetworkSettingsStore, RuntimeNetworkSettingsError
from .runtime_services import PostgresRuntimeServiceSettingsStore, RuntimeServiceSettingsError
from .vector_targets import PostgresVectorTargetStore, VectorTargetStoreError


@dataclass(frozen=True)
class RuntimeSetupState:
    configured: bool
    service_settings_configured: bool
    network_settings_configured: bool
    vector_target_configured: bool
    embedding_profile_configured: bool
    missing: tuple[str, ...]
    errors: tuple[str, ...] = ()


class RuntimeSettingsResolver:
    """Resolve Admin-owned runtime configuration over neutral bootstrap settings.

    P2-C makes VectorTarget the physical vector endpoint authority. P2-D makes
    EmbeddingProfile the embedding execution/vector-space authority. The runtime
    singleton only selects persistent IDs plus transitional VectorIndex fields.
    """

    def __init__(
        self,
        bootstrap: Settings,
        service_store: PostgresRuntimeServiceSettingsStore,
        network_store: PostgresRuntimeNetworkSettingsStore,
        vector_target_store: PostgresVectorTargetStore,
        embedding_profile_store: PostgresEmbeddingProfileStore,
        *,
        cache_ttl_seconds: float = 1.0,
    ) -> None:
        self._bootstrap = bootstrap
        self._service_store = service_store
        self._network_store = network_store
        self._vector_target_store = vector_target_store
        self._embedding_profile_store = embedding_profile_store
        self._cache_ttl_seconds = max(0.0, float(cache_ttl_seconds))
        self._lock = threading.RLock()
        self._cached: Settings = bootstrap
        self._cached_at = 0.0

    @staticmethod
    def _model_name_from_id(model_id: str, provider: str) -> str | None:
        prefix = f"{provider}:"
        if not model_id.startswith(prefix):
            return None
        value = model_id.removeprefix(prefix).strip()
        return unquote(value) if value else None

    @property
    def _tenant_id(self) -> str:
        return self._bootstrap.snapshot_tenant_id.strip() or "vision-default"

    def _selected_target(self, service: Any):
        if service is None or not service.vector.vector_target_id:
            return None
        try:
            target = self._vector_target_store.get(service.vector.vector_target_id)
        except VectorTargetStoreError:
            return None
        if (
            target is None
            or not target.enabled
            or target.engine != "qdrant"
            or target.tenant_id != self._tenant_id
        ):
            return None
        return target

    def _selected_profile(self, service: Any):
        if service is None or not service.vector.embedding_profile_id:
            return None
        try:
            profile = self._embedding_profile_store.get(
                service.vector.embedding_profile_id
            )
        except EmbeddingProfileStoreError:
            return None
        if (
            profile is None
            or not profile.enabled
            or profile.tenant_id != self._tenant_id
        ):
            return None
        return profile

    def _merge(self) -> Settings:
        resolved = self._bootstrap
        try:
            service = self._service_store.get(refresh=True)
        except RuntimeServiceSettingsError:
            service = self._service_store.cached()

        target = self._selected_target(service)
        profile = self._selected_profile(service)
        if service is not None:
            resolved = replace(
                resolved,
                vector_target_id=service.vector.vector_target_id,
                embedding_profile_id=service.vector.embedding_profile_id,
                qdrant_collection=service.vector.collection,
                index_version=service.vector.index_version,
                groq_base_url=service.groq.base_url,
                groq_model=service.groq.model,
                default_model_id=service.groq.default_model_id,
            )
            if target is not None:
                resolved = replace(
                    resolved,
                    vector_db_provider=target.engine,
                    vector_target_id=target.vector_target_id,
                    qdrant_url=target.endpoint,
                )
            if profile is not None:
                resolved = replace(
                    resolved,
                    embedding_profile_id=profile.embedding_profile_id,
                    embedding_deployment=profile.deployment,
                    embedding_provider=profile.provider,
                    embedding_base_url=profile.base_url,
                    embedding_model=profile.model,
                    embedding_model_id=profile.model_id,
                    embedding_dimension=profile.dimension,
                    embedding_batch_size=profile.batch_size,
                )

            backend_model = self._model_name_from_id(
                service.groq.default_model_id, "backendai"
            )
            nvidia_model = self._model_name_from_id(
                service.groq.default_model_id, "nvidia"
            )
            groq_model = self._model_name_from_id(
                service.groq.default_model_id, "groq"
            )
            if backend_model:
                resolved = replace(resolved, backendai_model=backend_model)
            if nvidia_model:
                resolved = replace(resolved, ai_model=nvidia_model)
            if groq_model:
                resolved = replace(resolved, groq_model=groq_model)

        try:
            network = self._network_store.get(refresh=True)
        except RuntimeNetworkSettingsError:
            network = self._network_store.cached()
        if network is not None:
            resolved = replace(
                resolved,
                frontend_host=network.frontend.ip,
                frontend_port=network.frontend.port,
                backendai_base_url=network.backendai.http_base_url,
            )
        return resolved

    def setup_state(self, *, refresh: bool = False) -> RuntimeSetupState:
        errors: list[str] = []
        try:
            service = self._service_store.get(refresh=refresh)
            service_configured = self._service_store.is_complete(service)
        except RuntimeServiceSettingsError:
            errors.append("service_settings_unavailable")
            service = self._service_store.cached()
            service_configured = self._service_store.is_complete(service)

        try:
            network_configured = self._network_store.configured(refresh=refresh)
        except RuntimeNetworkSettingsError:
            errors.append("network_settings_unavailable")
            cached_network = self._network_store.cached()
            network_configured = cached_network is not None

        target_configured = False
        if service is not None and service.vector.vector_target_id:
            try:
                target = self._vector_target_store.get(service.vector.vector_target_id)
                target_configured = bool(
                    target is not None
                    and target.enabled
                    and target.engine == "qdrant"
                    and target.tenant_id == self._tenant_id
                )
            except VectorTargetStoreError:
                errors.append("vector_target_unavailable")

        profile_configured = False
        if service is not None and service.vector.embedding_profile_id:
            try:
                profile = self._embedding_profile_store.get(
                    service.vector.embedding_profile_id
                )
                profile_configured = bool(
                    profile is not None
                    and profile.enabled
                    and profile.tenant_id == self._tenant_id
                )
            except EmbeddingProfileStoreError:
                errors.append("embedding_profile_unavailable")

        missing: list[str] = []
        if not network_configured:
            missing.append("network_settings")
        if not service_configured:
            missing.append("service_settings")
        if not target_configured:
            missing.append("vector_target")
        if not profile_configured:
            missing.append("embedding_profile")

        return RuntimeSetupState(
            configured=not missing,
            service_settings_configured=service_configured,
            network_settings_configured=network_configured,
            vector_target_configured=target_configured,
            embedding_profile_configured=profile_configured,
            missing=tuple(dict.fromkeys(missing)),
            errors=tuple(errors),
        )

    def current(self, *, force: bool = False) -> Settings:
        now = monotonic()
        with self._lock:
            if (
                not force
                and self._cached_at > 0
                and now - self._cached_at < self._cache_ttl_seconds
            ):
                return self._cached
            self._cached = self._merge()
            self._cached_at = now
            return self._cached

    def invalidate(self) -> None:
        with self._lock:
            self._cached_at = 0.0

    @property
    def bootstrap(self) -> Settings:
        return self._bootstrap


class RuntimeSettingsProxy:
    """Attribute-compatible dynamic view over the current runtime Settings."""

    def __init__(self, resolver: RuntimeSettingsResolver) -> None:
        object.__setattr__(self, "_resolver", resolver)

    def snapshot(self, *, force: bool = False) -> Settings:
        return self._resolver.current(force=force)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolver.current(), name)

    def __repr__(self) -> str:
        return f"RuntimeSettingsProxy({self._resolver.current()!r})"

