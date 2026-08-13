from __future__ import annotations

import json
import os

from backend.config import settings
from backend.domains.external_projects import (
    ExternalProjectRegistryService,
    PostgresExternalProjectRegistry,
)
from backend.integrations.vectordb.rag_lab import RagLabClient


def main() -> int:
    target_id = os.getenv("RAG_LAB_TARGET_ID", "rag-lab-main").strip() or "rag-lab-main"
    client = RagLabClient(
        settings.rag_lab_base_url,
        settings.rag_lab_token,
        settings.rag_lab_timeout_seconds,
    )
    service = ExternalProjectRegistryService(
        PostgresExternalProjectRegistry(settings),
        client,
        target_id=target_id,
        target_name="RAG Lab",
    )
    report = service.sync()
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if report.availability == "online" else 2


if __name__ == "__main__":
    raise SystemExit(main())
