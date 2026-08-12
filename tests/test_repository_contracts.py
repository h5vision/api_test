from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
def test_repository_contract_boundary_uses_canonical_repository_contracts():
    text=(ROOT/"backend/contracts/repositories.py").read_text(encoding="utf-8")
    assert "from .common import" in text
    assert "from .projects import GitVersionInfo" in text
    for name in ("RepositoryBrowserListResponse","RepositorySourceTreeResponse","UploadProgressResponse","IndexingJobListResponse"):
        assert name in text
