from pathlib import Path




ROOT = Path(__file__).resolve().parents[1]




def test_snapshot_frontend_is_a_real_route_module():
    main = (ROOT / "admin" / "src" / "main.ts").read_text(encoding="utf-8")
    snapshots = (ROOT / "admin" / "src" / "snapshots.ts").read_text(encoding="utf-8")


    assert 'from "./snapshots"' in main
    assert "SNAPSHOT_ROUTE" in main
    assert "initializeSnapshotAdmin(adminApiBaseUrl)" in main
    assert "snapshotAdminMarkup()" in main
    assert 'export const SNAPSHOT_ROUTE = "/snapshots"' in snapshots
    assert "/snapshots/status" in snapshots
    assert "/tree" in snapshots
    assert "/file?path=" in snapshots
    assert "X-Vision-Snapshot-Token" not in snapshots
    assert "SNAPSHOT_MVP_TOKEN" not in snapshots
    assert "Snapshot ???湲곕줉" not in main
