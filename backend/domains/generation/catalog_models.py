from __future__ import annotations

import urllib.parse

from ...ai_providers import AIProviderStoreError
from ...contracts.models import ModelInfo


class GenerationCatalogModelsMixin:
    def models(self) -> list[ModelInfo]:
        backendai_status = self.backendai_status()
        nvidia_status = self.nvidia_status()
        groq_status = self.groq_status()
        groq = self._groq_settings()
        default_model_id = groq.default_model_id
        backendai_models = backendai_status.get("models", [])
        nvidia_models = nvidia_status.get("models", [])
        groq_models = groq_status.get("models", [])
        selected_nvidia_model = self._preferred_catalog_model(
            self._parse_catalog_model_id(default_model_id, "nvidia")
            or self.settings.ai_model,
            nvidia_models,
        )
        selected_groq_model = self._preferred_catalog_model(
            self._parse_catalog_model_id(default_model_id, "groq") or groq.model,
            groq_models,
        )
        parsed_backendai_url = urllib.parse.urlparse(self._backendai_base_url())
        backendai_location = (
            "local"
            if (parsed_backendai_url.hostname or "").lower()
            in {"127.0.0.1", "localhost", "::1"}
            else "internal"
        )
        backendai_deployment = (
            "local" if backendai_location == "local" else "remote_server"
        )
        backendai_endpoint = self._endpoint_label(self._backendai_base_url())
        nvidia_endpoint = self._endpoint_label(self.settings.ai_base_url)
        groq_endpoint = self._endpoint_label(groq.base_url)
        models: list[ModelInfo] = []

        # Keep the legacy public aliases only when they resolve to a discovered
        # model. Newly selected defaults should use concrete discovered model IDs.
        if self.settings.backendai_model in backendai_models:
            models.append(
                ModelInfo(
                    model_id=self.settings.backendai_public_model_id,
                    model_name=self.settings.backendai_model,
                    display_name=f"Model Runtime ({self.settings.backendai_model})",
                    provider="backendai",
                    location=backendai_location,
                    deployment_type=backendai_deployment,
                    endpoint=backendai_endpoint,
                    enabled=self._model_enabled(self.settings.backendai_public_model_id),
                    available=True,
                    is_default=default_model_id == self.settings.backendai_public_model_id,
                    streaming=False,
                )
            )
        if selected_nvidia_model:
            models.append(
                ModelInfo(
                    model_id=self.settings.nvidia_public_model_id,
                    model_name=selected_nvidia_model,
                    display_name=f"Cloud Model ({selected_nvidia_model})",
                    provider="nvidia",
                    location="cloud",
                    deployment_type="cloud",
                    endpoint=nvidia_endpoint,
                    enabled=self._model_enabled(self.settings.nvidia_public_model_id),
                    available=True,
                    is_default=default_model_id == self.settings.nvidia_public_model_id,
                    streaming=False,
                )
            )
        if selected_groq_model:
            models.append(
                ModelInfo(
                    model_id=self.settings.groq_public_model_id,
                    model_name=selected_groq_model,
                    display_name=f"Cloud Model ({selected_groq_model})",
                    provider="groq",
                    location="cloud",
                    deployment_type="cloud",
                    endpoint=groq_endpoint,
                    enabled=self._model_enabled(self.settings.groq_public_model_id),
                    available=True,
                    is_default=default_model_id == self.settings.groq_public_model_id,
                    streaming=False,
                )
            )

        for model_name in backendai_models:
            model_id = f"backendai:{model_name}"
            models.append(
                ModelInfo(
                    model_id=model_id,
                    model_name=model_name,
                    display_name=f"Model Runtime ({model_name})",
                    provider="backendai",
                    location=backendai_location,
                    deployment_type=backendai_deployment,
                    endpoint=backendai_endpoint,
                    enabled=self._model_enabled(model_id),
                    available=True,
                    is_default=default_model_id == model_id,
                    streaming=False,
                )
            )
        for provider, model_names, endpoint in (
            ("nvidia", nvidia_models, nvidia_endpoint),
            ("groq", groq_models, groq_endpoint),
        ):
            for model_name in model_names:
                model_id = self._catalog_model_id(provider, model_name)
                models.append(
                    ModelInfo(
                        model_id=model_id,
                        model_name=model_name,
                        display_name=f"{provider.upper()} ({model_name})",
                        provider=provider,
                        location="cloud",
                        deployment_type="cloud",
                        endpoint=endpoint,
                        enabled=self._model_enabled(model_id),
                        available=True,
                        is_default=default_model_id == model_id,
                        streaming=False,
                    )
                )
        if self._custom_provider_registry is not None:
            try:
                models.extend(self._custom_provider_registry.models(default_model_id))
            except AIProviderStoreError:
                pass

        # Deduplicate by stable model_id while preserving the first presentation.
        unique: dict[str, ModelInfo] = {}
        for model in models:
            unique.setdefault(model.model_id, model)
        return list(unique.values())
