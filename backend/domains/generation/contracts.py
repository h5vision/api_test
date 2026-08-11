from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterator

@dataclass(frozen=True)
class GenerationResult:
    request_id: str
    answer: str
    requested_model_id: str
    used_model_id: str
    provider: str
    used_model_name: str

@dataclass(frozen=True)
class StreamingGeneration:
    request_id: str
    requested_model_id: str
    used_model_id: str
    provider: str
    used_model_name: str
    inference_protocol: str
    inference_endpoint: str | None
    deltas: Iterator[str]
