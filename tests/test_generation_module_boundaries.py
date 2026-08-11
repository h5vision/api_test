from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_METHODS = {
    "__init__", "_backendai_base_url", "_groq_settings", "default_model_id",
    "chat_processing_mode", "_model_enabled", "_endpoint_label",
    "invalidate_backendai_status", "invalidate_groq_status", "invalidate_nvidia_status",
    "backendai_status", "_probe_backendai", "_backendai_available",
    "_catalog_model_id", "_parse_catalog_model_id", "_preferred_catalog_model",
    "_probe_openai_catalog", "nvidia_status", "groq_status", "_probe_groq", "models",
    "_extract_answer", "generate", "_validate_external_messages", "stream_backendai",
    "_generate_nvidia", "_generate_groq",
}

def _methods(path: Path, class_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    return {node.name for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}

def test_generation_router_is_split_without_losing_methods():
    parts = [
        _methods(ROOT/"backend/domains/generation/catalog_core.py", "GenerationCatalogCoreMixin"),
        _methods(ROOT/"backend/domains/generation/catalog_status.py", "GenerationCatalogStatusMixin"),
        _methods(ROOT/"backend/domains/generation/catalog_ids.py", "GenerationCatalogIdsMixin"),
        _methods(ROOT/"backend/domains/generation/catalog_models.py", "GenerationCatalogModelsMixin"),
        _methods(ROOT/"backend/domains/generation/execution_common.py", "GenerationExecutionCommonMixin"),
        _methods(ROOT/"backend/domains/generation/execution_backendai.py", "GenerationBackendAIExecutionMixin"),
        _methods(ROOT/"backend/domains/generation/execution_cloud.py", "GenerationCloudExecutionMixin"),
        _methods(ROOT/"backend/domains/generation/routing.py", "GenerationRouter"),
    ]
    combined = set().union(*parts)
    assert combined == EXPECTED_METHODS
    for index, current in enumerate(parts):
        for other in parts[index + 1:]:
            assert current & other == set()

def test_generation_root_is_module_alias_for_monkeypatch_compatibility():
    text=(ROOT/"backend/generation.py").read_text(encoding="utf-8")
    assert "domains.generation import routing" in text
    assert "sys.modules[__name__] = _implementation" in text

def test_generation_contract_and_context_are_separate():
    contracts=(ROOT/"backend/domains/generation/contracts.py").read_text(encoding="utf-8")
    context=(ROOT/"backend/domains/generation/context.py").read_text(encoding="utf-8")
    assert "class GenerationResult" in contracts
    assert "class StreamingGeneration" in contracts
    assert "def passthrough_messages" in context
    assert "def _frontend_attachment_context" in context
