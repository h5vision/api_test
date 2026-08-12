from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from backend.frontend_clients import PostgresFrontendClientStore


def _client_row(*, client_id: str, instance_id: str, ip: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    return {
        "client_id": client_id,
        "instance_id": instance_id,
        "name": "Vision VS Code Extension",
        "ip": ip,
        "port": 8888,
        "enabled": True,
        "chat_deep_normalization_mode": "inherit",
        "registration_type": "auto",
        "last_seen_ip": ip,
        "last_seen_at": now,
        "created_at": now,
        "updated_at": now,
    }


class _Result:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, Any] | None:
        return self._row


class _Connection:
    def __init__(self, *, created: dict[str, Any]) -> None:
        self.created = created
        self.statements: list[str] = []

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: str, _params: object) -> _Result:
        normalized = " ".join(statement.split())
        self.statements.append(normalized)
        if "WHERE client_id = %s" in normalized:
            return _Result(None)
        if "WHERE instance_id = %s" in normalized:
            return _Result(None)
        if "WHERE ip = %s" in normalized:
            raise AssertionError(
                "A supplied VS Code instance ID must not fall back to source IP"
            )
        if "INSERT INTO frontend_clients" in normalized:
            return _Result(self.created)
        raise AssertionError(f"Unexpected SQL: {normalized}")


def test_new_instance_registers_separately_without_source_ip_fallback() -> None:
    instance_id = "machine-extension-hash"
    source_ip = "192.168.0.18"
    created = _client_row(
        client_id="fcli_new_instance",
        instance_id=instance_id,
        ip=source_ip,
    )
    connection = _Connection(created=created)
    store = PostgresFrontendClientStore(
        SimpleNamespace(frontend_port=8888)  # type: ignore[arg-type]
    )
    store._ensure_schema = lambda: None  # type: ignore[method-assign]
    store._connect = lambda: connection  # type: ignore[method-assign]

    decision = store.authorize_or_register(
        client_id=None,
        instance_id=instance_id,
        source_ip=source_ip,
        auto_register=True,
        client_name="Vision VS Code Extension",
    )

    assert decision.allowed is True
    assert decision.auto_registered is True
    assert decision.reason == "auto_registered"
    assert decision.client is not None
    assert decision.client.client_id == "fcli_new_instance"
    assert decision.client.instance_id == instance_id
    assert not any("WHERE ip = %s" in sql for sql in connection.statements)
