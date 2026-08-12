from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_app_module_owns_public_asgi_composition_without_module_replacement() -> None:
    source = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")

    assert "app = _legacy_app.app" in source
    assert "sys.modules[__name__] = _legacy_app" not in source
    assert "def __getattr__(name: str)" in source
    assert "def __dir__()" in source


def test_app_composition_keeps_canonical_router_boundaries() -> None:
    source = (ROOT / "backend" / "app.py").read_text(encoding="utf-8")

    for factory in (
        "create_models_router",
        "create_system_router",
        "create_projects_router",
        "create_snapshots_router",
        "create_repositories_router",
        "create_chat_router",
        "create_admin_router",
    ):
        assert factory in source

    assert "_remove_legacy_routes(" in source
    assert "_legacy_app.app.router.routes.extend(_admin_router.routes)" in source
