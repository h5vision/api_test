from pathlib import Path
import re

ADMIN_SRC = Path(__file__).resolve().parents[1] / "admin" / "src"
FORBIDDEN_HEADING_TERMS = (
    "POSTGRESQL",
    "QDRANT",
    "BackendAI",
    "AI Model Server",
    "Vector Store",
    "VectorDB",
    "sLLM Playground",
    "DB Vectorization",
)


def _heading_text(source: str) -> str:
    parts = re.findall(r"<h[1-3][^>]*>(.*?)</h[1-3]>", source, flags=re.S)
    parts += re.findall(r"<summary[^>]*>(.*?)</summary>", source, flags=re.S)
    parts += re.findall(r"sectionHeading\(\"([^\"]+)\"", source)
    cleaned = [re.sub(r"<[^>]+>", "", part) for part in parts]
    return "\n".join(cleaned)


def test_admin_section_names_are_role_based() -> None:
    for name in ("main.ts", "systemStatus.ts", "playground.ts", "snapshots.ts"):
        source = (ADMIN_SRC / name).read_text(encoding="utf-8")
        headings = _heading_text(source)
        for forbidden in FORBIDDEN_HEADING_TERMS:
            assert forbidden not in headings, f"{name} heading contains implementation name: {forbidden}"


def test_admin_exposes_persistence_capabilities() -> None:
    main = (ADMIN_SRC / "main.ts").read_text(encoding="utf-8")
    system = (ADMIN_SRC / "systemStatus.ts").read_text(encoding="utf-8")
    assert "영속 데이터 기능" in main
    assert "영속 데이터 기능 상태" in system
    assert "/persistence-status" in main
    assert "/persistence-status" in system

