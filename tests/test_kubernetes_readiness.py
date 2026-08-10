from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from backend.uploads import UploadManager


def test_upload_guard_uses_cross_replica_lock(tmp_path: Path) -> None:
    events: list[str] = []

    @contextmanager
    def lock_factory(name: str):
        events.append(f"enter:{name}")
        try:
            yield
        finally:
            events.append(f"exit:{name}")

    manager = UploadManager(
        SimpleNamespace(upload_root=tmp_path),
        lock_factory=lock_factory,
    )
    with manager._guard("upl_0123456789abcdef0123456789abcdef"):
        events.append("body")

    assert events == [
        "enter:upload:upl_0123456789abcdef0123456789abcdef",
        "body",
        "exit:upload:upl_0123456789abcdef0123456789abcdef",
    ]
    assert manager.storage_status()["distributed_lock"] is True


def test_kubernetes_contract_files_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    k8s = root / "deploy" / "kubernetes"
    expected = {
        "api.yaml",
        "worker.yaml",
        "migration-job.yaml",
        "shared-workspace-pvc.yaml",
        "ingress.yaml",
        "keda-worker.yaml",
        "kustomization.yaml",
    }
    assert expected <= {path.name for path in k8s.iterdir()}

    worker = (k8s / "worker.yaml").read_text(encoding="utf-8")
    assert "terminationGracePeriodSeconds" in worker
    assert "backend.worker_probe" in worker
    assert "vision-shared-workspace" in worker

    scaler = (k8s / "keda-worker.yaml").read_text(encoding="utf-8")
    assert "type: redis-streams" in scaler
    assert "consumerGroup: vision:tasks:indexing:workers" in scaler
    assert "lagCount:" in scaler

