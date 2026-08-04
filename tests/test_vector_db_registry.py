from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from backend.schemas import VectorDatabaseProviderWriteRequest
from backend.vector_db_registry import VectorDatabaseDetector


class VectorDatabaseRegistryTests(unittest.TestCase):
    def test_vector_database_contract_accepts_common_registration_fields(self) -> None:
        value = VectorDatabaseProviderWriteRequest(
            name="Remote VectorDB",
            engine="auto",
            host="192.168.0.12",
            port=6333,
            storage_namespace="vision_bge_m3_v1",
            embedding_model_path="http://192.168.0.12:11434",
            embedding_models=["provider:aip_test:bge-m3:latest"],
        )

        self.assertEqual(value.host, "192.168.0.12")
        self.assertEqual(value.embedding_models, ["provider:aip_test:bge-m3:latest"])

    def test_vector_database_requires_an_embedding_model(self) -> None:
        with self.assertRaises(ValidationError):
            VectorDatabaseProviderWriteRequest(
                name="Remote VectorDB",
                engine="qdrant",
                host="qdrant",
                port=6333,
                storage_namespace="vision",
                embedding_model_path="http://ollama:11434",
                embedding_models=[],
            )

    def test_auto_detection_identifies_qdrant_and_collections(self) -> None:
        provider = SimpleNamespace(
            enabled=True,
            engine="auto",
            connection_mode="remote",
            base_url="http://qdrant:6333",
        )
        detector = VectorDatabaseDetector()
        with patch.object(
            detector,
            "_request",
            return_value=(
                200,
                {"result": {"collections": [{"name": "vision"}]}},
            ),
        ):
            result = detector.probe(provider)

        self.assertEqual(result.status, "online")
        self.assertEqual(result.detected_engine, "qdrant")
        self.assertEqual(result.collections, ["vision"])
        self.assertTrue(result.adapter_available)

    def test_auto_detection_identifies_rag_lab_and_projects(self) -> None:
        provider = SimpleNamespace(
            enabled=True,
            engine="auto",
            connection_mode="remote",
            base_url="http://192.0.2.12:8200",
        )
        detector = VectorDatabaseDetector()

        def response(_base_url, _method, path, **_kwargs):
            if path == "/health":
                return 200, {"ok": True, "embed_model": "bge-m3:latest"}
            if path == "/projects":
                return 200, {"projects": [{"project_id": "fest-api"}]}
            return 404, None

        with patch.object(detector, "_request", side_effect=response):
            result = detector.probe(provider)

        self.assertEqual(result.status, "online")
        self.assertEqual(result.detected_engine, "rag_lab")
        self.assertEqual(result.collections, ["fest-api"])
        self.assertTrue(result.adapter_available)

    def test_pgvector_is_registered_but_not_claimed_as_runtime_ready(self) -> None:
        provider = SimpleNamespace(
            enabled=True,
            engine="pgvector",
            connection_mode="remote",
            base_url="http://postgres:5432",
        )

        result = VectorDatabaseDetector().probe(provider)

        self.assertEqual(result.status, "degraded")
        self.assertFalse(result.adapter_available)
        self.assertEqual(result.error, "pgvector_adapter_not_implemented")

    def test_local_sqlite_target_uses_path_instead_of_host_and_port(self) -> None:
        value = VectorDatabaseProviderWriteRequest(
            name="Local SQLite",
            engine="sqlite",
            connection_mode="local",
            local_path="project-a/vector.sqlite3",
            storage_namespace="project-a",
            embedding_model_path="http://ollama:11434",
            embedding_models=["provider:aip_test:bge-m3:latest"],
        )

        self.assertIsNone(value.host)
        self.assertIsNone(value.port)
        self.assertEqual(value.local_path, "project-a/vector.sqlite3")


if __name__ == "__main__":
    unittest.main()
