from __future__ import annotations

import unittest
from types import SimpleNamespace

from backend.model_access import PostgresModelAccessPolicyStore


class ModelAccessPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        settings = SimpleNamespace(
            backendai_public_model_id="backendai-default",
            nvidia_public_model_id="nvidia-default",
            groq_public_model_id="groq-default",
        )
        self.store = PostgresModelAccessPolicyStore(settings)

    def test_discovered_provider_models_are_enabled_by_default(self) -> None:
        for model_id in (
            "backendai:qwen2.5-coder%3A7b",
            "nvidia:meta%2Fllama-3.1-70b-instruct",
            "groq:llama-3.3-70b-versatile",
            "provider:aip_123:model",
        ):
            with self.subTest(model_id=model_id):
                self.assertTrue(self.store._default_enabled(model_id))

    def test_unknown_non_provider_model_is_disabled_by_default(self) -> None:
        self.assertFalse(self.store._default_enabled("unknown-model"))


if __name__ == "__main__":
    unittest.main()
