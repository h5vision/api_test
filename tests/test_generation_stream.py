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

        with patch("backend.generation.urllib.request.urlopen", return_value=response):
            stream = router.stream_backendai(
                "backendai-default",
                [{"role": "user", "content": "question"}],
                request_id="req-stream-test",
            )
            answer = "".join(stream.deltas)

        self.assertEqual(answer, "hello world")
        self.assertEqual(stream.inference_protocol, "ollama")
        self.assertEqual(stream.inference_endpoint, "192.0.2.10:11500")


if __name__ == "__main__":
    unittest.main()
