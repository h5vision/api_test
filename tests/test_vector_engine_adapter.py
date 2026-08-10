from __future__ import annotations

from backend.vector_store import (
    QdrantVectorAdapter,
    VectorIndexRef,
    VectorIndexSpec,
    VectorQuery,
    VectorSelector,
)


class RecordingQdrant(QdrantVectorAdapter):
    def __init__(self, responses):
        super().__init__("http://qdrant.invalid", "", 1)
        self.responses = list(responses)
        self.calls = []

    def _request(self, method, path, payload=None, **kwargs):
        self.calls.append((method, path, payload, kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected request: {method} {path}")
        return self.responses.pop(0)


def test_describe_missing_index_never_provisions_it():
    adapter = RecordingQdrant([{"status": "not_found"}])
    state = adapter.describe_index(VectorIndexRef("missing"))
    assert state.exists is False
    assert [call[0] for call in adapter.calls] == ["GET"]


def test_provision_is_the_only_collection_creation_path():
    adapter = RecordingQdrant(
        [
            {"status": "not_found"},
            {"result": {"status": "ok"}},
            {
                "result": {
                    "status": "green",
                    "points_count": 0,
                    "config": {
                        "params": {
                            "vectors": {"size": 1024, "distance": "Cosine"}
                        }
                    },
                }
            },
        ]
    )
    state = adapter.provision_index(
        VectorIndexSpec("vision_vectors", 1024, "cosine")
    )
    assert state.exists is True
    assert [call[0] for call in adapter.calls] == ["GET", "PUT", "GET"]


def test_query_combines_index_and_operation_selectors():
    adapter = RecordingQdrant(
        [
            {
                "result": {
                    "status": "green",
                    "points_count": 1,
                    "config": {
                        "params": {
                            "vectors": {"size": 3, "distance": "Cosine"}
                        }
                    },
                }
            },
            {
                "result": {
                    "points": [
                        {"id": "p1", "score": 0.9, "payload": {"content": "x"}}
                    ]
                }
            },
        ]
    )
    matches = adapter.query(
        VectorIndexRef("vision_vectors", VectorSelector({"tenant": "t1"})),
        VectorQuery(
            vector=[1.0, 0.0, 0.0],
            top_k=5,
            selector=VectorSelector({"language": "python"}),
        ),
    )
    query_payload = adapter.calls[1][2]
    must = query_payload["filter"]["must"]
    assert {item["key"] for item in must} == {"tenant", "language"}
    assert matches[0].point_id == "p1"

