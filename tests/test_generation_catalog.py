from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from backend.generation import GenerationRouter


class _Response:
    def __init__(self, payload: dict[str, object], status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class GenerationCatalogTests(unittest.TestCase):
    def test_catalog_model_id_round_trip(self) -> None:
        model_name = "meta/llama-3.1-70b-instruct"
        model_id = GenerationRouter._catalog_model_id("nvidia", model_name)

        self.assertEqual(
            GenerationRouter._parse_catalog_model_id(model_id, "nvidia"),
            model_name,
        )
        self.assertIsNone(
            GenerationRouter._parse_catalog_model_id(model_id, "groq")
        )

    def test_preferred_model_uses_configured_model_when_discovered(self) -> None:
        models = ["a/model", "meta/llama-3.1-70b-instruct"]

        selected = GenerationRouter._preferred_catalog_model(
            "meta/llama-3.1-70b-instruct",
            models,
        )

        self.assertEqual(selected, "meta/llama-3.1-70b-instruct")

    def test_preferred_model_falls_back_to_discovered_catalog(self) -> None:
        selected = GenerationRouter._preferred_catalog_model(
            "removed/model",
            ["a/model", "b/model"],
        )

        self.assertEqual(selected, "a/model")

    @patch("backend.generation.urllib.request.urlopen")
    def test_openai_catalog_discovers_and_sorts_models(
        self,
        urlopen: object,
    ) -> None:
        urlopen.return_value = _Response(
            {
                "data": [
                    {"id": "z/model"},
                    {"id": "a/model"},
                    {"id": "a/model"},
                    {"missing": "id"},
                ]
            }
        )

        status = GenerationRouter._probe_openai_catalog(
            "https://provider.example/v1",
            "test-key",
            timeout_seconds=3,
        )

        self.assertEqual(status["status"], "online")
        self.assertTrue(status["model_available"])
        self.assertEqual(status["models"], ["a/model", "z/model"])

    @patch("backend.generation.urllib.request.urlopen")
    def test_openai_catalog_reports_invalid_key(
        self,
        urlopen: object,
    ) -> None:
        urlopen.side_effect = urllib.error.HTTPError(
            "https://provider.example/v1/models",
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"error":"invalid_api_key"}'),
        )

        status = GenerationRouter._probe_openai_catalog(
            "https://provider.example/v1",
            "invalid-key",
            timeout_seconds=3,
        )

        self.assertEqual(status["status"], "offline")
        self.assertFalse(status["model_available"])
        self.assertEqual(status["models"], [])
        self.assertEqual(status["error"], "http_401")


if __name__ == "__main__":
    unittest.main()
