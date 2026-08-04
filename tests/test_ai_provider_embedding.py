from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from backend.ai_providers import AIProviderRegistry
from backend.schemas import (
    AIProviderWriteRequest,
    CloudProviderCredentialRequest,
    RuntimeVectorSettingsWrite,
)
from backend.services import EmbeddingService


class AIProviderEmbeddingDiscoveryTests(unittest.TestCase):
    def test_known_cloud_provider_only_requires_api_key(self) -> None:
        value = CloudProviderCredentialRequest(
            provider_type="nvidia",
            api_key="secret-key",
        )

        self.assertIsNone(value.base_url)
        self.assertEqual(value.auth_type, "bearer")

    def test_custom_cloud_provider_requires_base_url(self) -> None:
        with self.assertRaises(ValidationError):
            CloudProviderCredentialRequest(
                provider_type="custom",
                api_key="secret-key",
            )

    def test_common_embedding_model_names_are_detected(self) -> None:
        for model_name in (
            "bge-m3:latest",
            "nomic-embed-text",
            "text-embedding-3-small",
            "nv-embedqa-e5-v5",
        ):
            with self.subTest(model_name=model_name):
                self.assertTrue(
                    AIProviderRegistry._looks_like_embedding_model(model_name)
                )
        self.assertFalse(
            AIProviderRegistry._looks_like_embedding_model("llama3.3:70b")
        )

    def test_catalog_capabilities_separate_chat_and_embedding_models(self) -> None:
        self.assertEqual(
            AIProviderRegistry._catalog_capabilities(
                {"id": "bge-m3", "capabilities": ["embedding"]},
                "bge-m3",
            ),
            (False, True),
        )
        self.assertEqual(
            AIProviderRegistry._catalog_capabilities(
                {"id": "qwen2.5", "capabilities": ["completion", "tools"]},
                "qwen2.5",
            ),
            (True, False),
        )
        self.assertEqual(
            AIProviderRegistry._catalog_capabilities(
                {"id": "nvidia/llama-3.2-nv-rerankqa-1b-v2"},
                "nvidia/llama-3.2-nv-rerankqa-1b-v2",
            ),
            (False, False),
        )

    def test_provider_host_and_port_resolve_to_embedding_base_url(self) -> None:
        value = AIProviderWriteRequest(
            name="LAN Ollama",
            protocol="ollama",
            host="192.168.0.12",
            port=11500,
        )

        self.assertEqual(value.resolved_base_url(), "http://192.168.0.12:11500")

    def test_runtime_vector_contract_accepts_selected_provider(self) -> None:
        value = RuntimeVectorSettingsWrite(
            host="qdrant",
            port=6333,
            collection="vision",
            embedding_deployment="api",
            embedding_provider="openai",
            embedding_provider_id="aip_test",
            embedding_base_url="https://provider.example/v1",
            embedding_model="text-embedding-3-small",
            embedding_model_id="aip_test:text-embedding-3-small",
            embedding_dimension=1536,
            embedding_batch_size=32,
            index_version="text-embedding-3-small-1536-v1",
        )

        self.assertEqual(value.embedding_provider_id, "aip_test")
        self.assertEqual(value.embedding_provider, "openai")

    def test_embedding_service_reuses_selected_provider_api_key(self) -> None:
        settings = SimpleNamespace(
            embedding_provider_id="aip_test",
            embedding_provider="ollama",
            embedding_base_url="http://old.example",
            embedding_api_key="",
            embedding_model="text-embedding-3-small",
            embedding_dimension=3,
            embedding_keep_alive="10m",
            embedding_timeout_seconds=30,
            request_timeout_seconds=30,
            allow_local_fallback=False,
        )
        provider = SimpleNamespace(
            enabled=True,
            protocol="openai",
            base_url="https://provider.example/v1",
            api_key="provider-secret",
            auth_type="x-api-key",
        )
        service = EmbeddingService(settings, lambda _provider_id: provider)

        with patch(
            "backend.services._post_json",
            return_value={"data": [{"embedding": [0.1, 0.2, 0.3]}]},
        ) as post_json:
            result = service.embed("hello", "query")

        self.assertEqual(result.provider, "openai")
        self.assertEqual(result.vector, [0.1, 0.2, 0.3])
        self.assertEqual(post_json.call_args.args[0], "https://provider.example/v1/embeddings")
        self.assertEqual(post_json.call_args.args[2], "provider-secret")
        self.assertEqual(post_json.call_args.args[4], "x-api-key")


if __name__ == "__main__":
    unittest.main()
