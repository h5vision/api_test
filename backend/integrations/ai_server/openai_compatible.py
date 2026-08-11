from __future__ import annotations

import json
import urllib.error
import urllib.request
from time import perf_counter
from typing import Any

from ...services import _post_json


def probe_model_catalog(
    base_url: str,
    api_key: str,
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    if not api_key:
        return {
            "status": "offline",
            "connected": False,
            "model_available": False,
            "models": [],
            "latency_ms": 0,
            "error": "missing_api_key",
        }
    if not base_url.strip():
        return {
            "status": "offline",
            "connected": False,
            "model_available": False,
            "models": [],
            "latency_ms": 0,
            "error": "missing_base_url",
        }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/models",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "VisionBackend/1.0",
        },
        method="GET",
    )
    started_at = perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        entries = payload.get("data", [])
        if not isinstance(entries, list):
            raise ValueError("models is not a list")
        models = sorted({
            str(item.get("id") or "").strip()
            for item in entries
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        })
        return {
            "status": "online" if models else "degraded",
            "connected": True,
            "model_available": bool(models),
            "models": models,
            "latency_ms": round((perf_counter() - started_at) * 1000),
            "error": None if models else "no_models",
        }
    except urllib.error.HTTPError as exc:
        return {
            "status": "offline", "connected": False, "model_available": False,
            "models": [], "latency_ms": round((perf_counter() - started_at) * 1000),
            "error": f"http_{exc.code}",
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


def chat_completion(
    base_url: str,
    payload: dict[str, Any],
    api_key: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    return _post_json(
        f"{base_url}/chat/completions",
        payload,
        api_key,
        timeout_seconds,
    )
