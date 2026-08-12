from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADMIN_SRC = ROOT / "admin" / "src"


def test_admin_entrypoint_uses_page_feature_and_shared_boundaries() -> None:
    source = (ADMIN_SRC / "main.ts").read_text(encoding="utf-8")

    assert 'from "./pages/playground"' in source
    assert 'from "./pages/snapshots"' in source
    assert 'from "./pages/system-status"' in source
    assert 'from "./shared/api"' in source
    assert 'from "./shared/components"' in source
    assert 'from "./shared/utils"' in source
    assert 'from "./features/frontend-clients"' in source
    assert 'from "./features/model-catalog"' in source
    assert 'from "./features/vector-targets"' in source


def test_admin_entrypoint_no_longer_owns_extracted_contracts() -> None:
    source = (ADMIN_SRC / "main.ts").read_text(encoding="utf-8")

    for declaration in (
        "type ProviderStatus =",
        "type FrontendClientRecord =",
        "type ModelInfo =",
        "type VectorRouteCandidate =",
        "const apiBaseUrl =",
        "const adminApiBaseUrl =",
    ):
        assert declaration not in source


def test_admin_scaffold_modules_are_real_nonempty_owners() -> None:
    modules = (
        "features/chat-sessions/index.ts",
        "features/frontend-clients/index.ts",
        "features/model-catalog/index.ts",
        "features/vector-targets/index.ts",
        "pages/overview/index.ts",
        "pages/playground/index.ts",
        "pages/snapshots/index.ts",
        "pages/system-status/index.ts",
        "shared/api/index.ts",
        "shared/components/index.ts",
        "shared/types/index.ts",
        "shared/utils/index.ts",
    )

    for relative_path in modules:
        assert (ADMIN_SRC / relative_path).read_text(encoding="utf-8").strip()
