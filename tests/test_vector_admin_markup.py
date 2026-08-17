from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_vector_management_is_extracted_without_changing_runtime_dom_ids() -> None:
    main = (ROOT / "admin" / "src" / "main.ts").read_text(encoding="utf-8")
    markup = (
        ROOT / "admin" / "src" / "features" / "vector-targets" / "markup.ts"
    ).read_text(encoding="utf-8")

    assert 'from "./features/vector-targets/markup"' in main
    assert "${vectorManagementMarkup()}" in main

    required_ids = {
        "vector-settings-form",
        "vector-host",
        "vector-port",
        "embedding-base-url",
        "embedding-model",
        "embedding-model-id",
        "embedding-dimension",
        "vector-collection",
        "index-version",
        "vector-route-project",
        "vector-route-binding",
        "vector-route-load",
        "vector-route-apply",
        "vector-route-clear",
        "reembed-button",
        "embedding-artifact-list",
        "indexing-job-list",
    }
    for element_id in required_ids:
        assert f'id="{element_id}"' in markup


def test_vector_management_markup_explains_the_three_runtime_steps() -> None:
    markup = (
        ROOT / "admin" / "src" / "features" / "vector-targets" / "markup.ts"
    ).read_text(encoding="utf-8")

    assert "1. VectorDB 연결" in markup
    assert "2. Embedding 공간" in markup
    assert "3. Index 식별" in markup
    assert "프로젝트 검색 Route" in markup
