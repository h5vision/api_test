from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from time import perf_counter
from typing import Any

from ...services import ServiceError, _post_json


def probe_model_catalog(
    base_url: str,
    api_key: str,
    *,
    timeout_seconds: int,
    selected_model: str | None,
    preferred_model: str | None,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        f"{base_url}/api/tags",
        headers=headers,
        method="GET",
    )
    started_at = perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            if not 200 <= response.status < 300:
                return {
                    "status": "offline", "connected": False, "model_available": False,
                    "latency_ms": round((perf_counter() - started_at) * 1000),
                    "error": f"http_{response.status}",
                }
            data = json.loads(response.read().decode("utf-8"))
            models = data.get("models", [])
            model_names: list[str] = []
            skipped_models: list[str] = []
            for model in models:
                if not isinstance(model, dict):
                    continue
                model_name = str(model.get("name") or model.get("model") or "").strip()
                if not model_name:
                    continue
                capabilities = model.get("capabilities")
                if (
                    isinstance(capabilities, list)
                    and capabilities
                    and "completion" not in {
                        str(capability).strip().lower()
                        for capability in capabilities
                    }
                ):
                    skipped_models.append(model_name)
                    continue
                model_names.append(model_name)
            model_names = sorted(set(model_names))
            selected = selected_model
            if selected is None and preferred_model in model_names:
                selected = preferred_model
            model_available = bool(model_names) if selected is None else selected in model_names
            return {
                "status": "online" if model_names else "degraded",
                "connected": True,
                "model_available": model_available,
                "selected_model": selected,
                "models": model_names,
                "skipped_non_chat_models": sorted(set(skipped_models)),
                "latency_ms": round((perf_counter() - started_at) * 1000),
                "error": None if model_names else "no_chat_models",
            }
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        reason = "timeout" if isinstance(exc, TimeoutError) else "unreachable"
        if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError):
            reason = "timeout"
        return {
            "status": "offline", "connected": False, "model_available": False,
            "models": [], "latency_ms": round((perf_counter() - started_at) * 1000),
            "error": reason,
        }


def chat(
    base_url: str,
    payload: dict[str, Any],
    api_key: str,
    timeout_seconds: int,
    *,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    return _post_json(
        f"{base_url}/api/chat",
        payload,
        api_key,
        timeout_seconds,
        extra_headers=extra_headers,
    )


def stream_chat(
    base_url: str,
    payload: dict[str, Any],
    api_key: str,
    timeout_seconds: int,
) -> Iterator[str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/x-ndjson",
        "User-Agent": "VisionBackend/1.0",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    upstream_request = urllib.request.Request(
        f"{base_url}/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    emitted = False
    try:
        with urllib.request.urlopen(upstream_request, timeout=timeout_seconds) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                data = json.loads(line)
                if data.get("error"):
                    raise ServiceError(
                        f"Ollama streaming error: {data['error']}",
                        status_code=502,
                    )
                message = data.get("message")
                content = message.get("content") if isinstance(message, dict) else None
                if isinstance(content, str) and content:
                    emitted = True
                    yield content
                if data.get("done"):
                    break
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise ServiceError(
            f"Ollama streaming HTTP {exc.code}: {detail}",
            status_code=502,
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ServiceError(
            f"Ollama streaming 연결 실패: {exc}",
            status_code=504 if isinstance(exc, TimeoutError) else 503,
        ) from exc
    except json.JSONDecodeError as exc:
        raise ServiceError("Ollama streaming 응답이 올바른 NDJSON이 아닙니다.") from exc
    if not emitted:
        raise ServiceError("Ollama streaming 응답에 텍스트가 없습니다.")
