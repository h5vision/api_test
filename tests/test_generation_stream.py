from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from backend.config import settings
from backend.generation import GenerationRouter


class _FakeResponse:
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def __iter__(self):
        return iter(self.lines)


class BackendAIStreamingTests(unittest.TestCase):
    def test_ollama_ndjson_is_emitted_as_text_deltas(self) -> None:
        configured = replace(
            settings,
            backendai_base_url="http://192.0.2.10:11500",
            backendai_model="qwen-test:7b",
            backendai_public_model_id="backendai-default",
        )
        router = GenerationRouter(configured)
        router.backendai_status = lambda force=False: {
            "connected": True,
            "models": ["qwen-test:7b"],
        }
        response = _FakeResponse(
            [
                b'{"message":{"content":"hello"},"done":false}\n',
                b'{"message":{"content":" world"},"done":false}\n',
                b'{"message":{"content":""},"done":true}\n',
            ]
        )

        with patch("backend.integrations.ai_server.ollama.urllib.request.urlopen", return_value=response):
            stream = router.stream_backendai(
                "backendai-default",
                [{"role": "user", "content": "question"}],
                request_id="req-stream-test",
            )
            answer = "".join(stream.deltas)

        self.assertEqual(answer, "hello world")
        self.assertEqual(stream.inference_protocol, "ollama")
        self.assertEqual(stream.inference_endpoint, "192.0.2.10:11500")

    def test_generate_callback_uses_the_real_ollama_stream(self) -> None:
        configured = replace(
            settings,
            backendai_base_url="http://192.0.2.10:11500",
            backendai_model="",
            backendai_public_model_id="backendai-default",
        )
        router = GenerationRouter(configured)
        router.backendai_status = lambda force=False: {
            "connected": True,
            "models": ["auto-detected:7b"],
        }
        response = _FakeResponse(
            [
                b'{"message":{"content":"real"},"done":false}\n',
                b'{"message":{"content":" stream"},"done":true}\n',
            ]
        )
        observed: list[str] = []

        with patch("backend.integrations.ai_server.ollama.urllib.request.urlopen", return_value=response):
            result = router.generate(
                "backendai-default",
                "question",
                [],
                [],
                "",
                "__unscoped__",
                "session",
                request_id="req-callback",
                delta_callback=observed.append,
            )

        self.assertEqual(observed, ["real", " stream"])
        self.assertEqual(result.answer, "real stream")
        self.assertEqual(result.used_model_name, "auto-detected:7b")


if __name__ == "__main__":
    unittest.main()
