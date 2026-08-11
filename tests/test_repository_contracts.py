from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_repository_contract_boundary_keeps_schema_identity_for_later_cleanup():
    text=(ROOT/"backend/contracts/repositories.py").read_text(encoding="utf-8")
    assert "from ..schemas import (" in text
    for name in ("RepositoryBrowserListResponse","RepositorySourceTreeResponse","UploadProgressResponse","IndexingJobListResponse"): assert name in text
