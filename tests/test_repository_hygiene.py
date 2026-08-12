from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_legacy_numbered_root_copies_are_not_restored() -> None:
    assert not (ROOT / "main (1).py").exists()
    assert not (ROOT / "requirements (1).txt").exists()


def test_script_repair_backups_are_not_versioned_as_source() -> None:
    scripts = ROOT / "scripts"
    assert list(scripts.glob("*.before-*")) == []


def test_generated_backup_locations_are_ignored() -> None:
    rules = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert ".snapshot-admin-backup/" in rules
    assert "scripts/*.before-*" in rules
