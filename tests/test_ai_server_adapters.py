from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class _ServiceError(RuntimeError):
    def __init__(self, message, status_code=500):
        super().__init__(message); self.status_code=status_code

def _load(name: str, file_name: str):
    backend=types.ModuleType("backend"); backend.__path__=[str(ROOT/"backend")]
    integrations=types.ModuleType("backend.integrations"); integrations.__path__=[str(ROOT/"backend"/"integrations")]
    ai=types.ModuleType("backend.integrations.ai_server"); ai.__path__=[str(ROOT/"backend"/"integrations"/"ai_server")]
    services=types.ModuleType("backend.services")
    services.ServiceError=_ServiceError
    services._post_json=lambda *args,**kwargs: {"unset": True}
    sys.modules.update({"backend":backend,"backend.integrations":integrations,"backend.integrations.ai_server":ai,"backend.services":services})
    spec=importlib.util.spec_from_file_location(name, ROOT/"backend"/"integrations"/"ai_server"/file_name)
    mod=importlib.util.module_from_spec(spec); sys.modules[name]=mod; spec.loader.exec_module(mod)
    return mod

def test_openai_chat_completion_preserves_transport_arguments():
    mod=_load("backend.integrations.ai_server.openai_compatible","openai_compatible.py")
    calls=[]
    mod._post_json=lambda *args,**kwargs: calls.append((args,kwargs)) or {"ok":True}
    payload={"model":"m","messages":[]}
    assert mod.chat_completion("https://example.test/v1",payload,"key",17)=={"ok":True}
    assert calls==[(("https://example.test/v1/chat/completions",payload,"key",17),{})]

def test_ollama_chat_preserves_transport_arguments():
    mod=_load("backend.integrations.ai_server.ollama","ollama.py")
    calls=[]
    mod._post_json=lambda *args,**kwargs: calls.append((args,kwargs)) or {"message":{"content":"ok"}}
    payload={"model":"m","messages":[]}
    headers={"X-Vision-Project-Id":"p"}
    result=mod.chat("http://localhost:11434",payload,"key",19,extra_headers=headers)
    assert result["message"]["content"]=="ok"
    assert calls==[(("http://localhost:11434/api/chat",payload,"key",19),{"extra_headers":headers})]

def test_ollama_stream_yields_ndjson_content(monkeypatch):
    mod=_load("backend.integrations.ai_server.ollama","ollama.py")
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def __iter__(self):
            yield b'{"message":{"content":"hel"}}\n'
            yield b'{"message":{"content":"lo"},"done":true}\n'
    monkeypatch.setattr(mod.urllib.request,"urlopen",lambda request,timeout: Response())
    assert list(mod.stream_chat("http://localhost:11434",{"model":"m"},"",10))==["hel","lo"]

def test_ollama_probe_preserves_preferred_model_fallback_semantics(monkeypatch):
    mod=_load("backend.integrations.ai_server.ollama","ollama.py")
    class Response:
        status=200
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def read(self):
            return b'{"models":[{"name":"available","capabilities":["completion"]}]}'
    monkeypatch.setattr(mod.urllib.request,"urlopen",lambda request,timeout: Response())
    result=mod.probe_model_catalog(
        "http://localhost:11434", "", timeout_seconds=2,
        selected_model=None, preferred_model="missing",
    )
    assert result["selected_model"] is None
    assert result["model_available"] is True
