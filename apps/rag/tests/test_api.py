import os

os.environ.update(
    {
        "OTEL_SDK_DISABLED": "true",
        "INGEST_API_KEY": "sk-ingest",
        "QUERY_API_KEY": "sk-query",
        "AUTH_DISABLED": "false",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app, get_service, get_settings  # noqa: E402
from app.rag import RagAnswer, answer_json_schema  # noqa: E402

INGEST = {"Authorization": "Bearer sk-ingest"}
QUERY = {"Authorization": "Bearer sk-query"}


class FakeService:
    def __init__(self):
        self.docs = {}

    def ingest(self, doc_id, text, source=None):
        self.docs[doc_id] = 2
        return 2

    def count(self, doc_id):
        return self.docs.get(doc_id, 0)

    def delete(self, doc_id):
        self.docs.pop(doc_id, None)

    def ask(self, question, top_k=None):
        if question == "panne":
            raise ConnectionError("llm down")
        return {"answer": "42", "sources": ["S1"], "confidence": "high", "citations": [], "usage": None}


@pytest.fixture
def client():
    get_settings.cache_clear()
    app.dependency_overrides[get_service] = FakeService
    fake = FakeService()
    app.dependency_overrides[get_service] = lambda: fake
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_healthz_is_public(client):
    assert client.get("/healthz").json() == {"status": "ok"}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer faux"}, QUERY])
def test_ingest_requires_the_write_key(client, headers):
    resp = client.post("/ingest", json={"documents": [{"id": "a", "text": "x"}]}, headers=headers)
    assert resp.status_code == 401


def test_ingest_counts_chunks(client):
    resp = client.post(
        "/ingest", json={"documents": [{"id": "a", "text": "x"}, {"id": "b", "text": "y"}]}, headers=INGEST
    )
    assert resp.status_code == 200
    assert resp.json()["chunks"] == 4


def test_document_lifecycle(client):
    client.post("/ingest", json={"documents": [{"id": "doc", "text": "x"}]}, headers=INGEST)
    assert client.get("/documents/doc", headers=INGEST).json() == {"id": "doc", "chunks": 2}
    assert client.delete("/documents/doc", headers=INGEST).status_code == 204
    assert client.get("/documents/doc", headers=INGEST).status_code == 404
    assert client.delete("/documents/doc", headers=QUERY).status_code == 401


@pytest.mark.parametrize("headers", [QUERY, INGEST])
def test_ask_accepts_read_and_write_keys(client, headers):
    resp = client.post("/ask", json={"question": "Quelle est la réponse ?"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["confidence"] == "high"


def test_ask_without_key_is_refused(client):
    assert client.post("/ask", json={"question": "Quelle est la réponse ?"}).status_code == 401


def test_ask_validates_input(client):
    assert client.post("/ask", json={"question": "a"}, headers=QUERY).status_code == 422


def test_upstream_failure_is_502(client):
    assert client.post("/ask", json={"question": "panne"}, headers=QUERY).status_code == 502


def test_startup_fails_without_keys(monkeypatch):
    monkeypatch.setenv("INGEST_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="obligatoires"):
        with TestClient(app):
            pass
    get_settings.cache_clear()


def test_schema_is_strict_for_constrained_decoding():
    fmt = answer_json_schema()
    schema = fmt["json_schema"]["schema"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"answer", "sources", "confidence"}
    assert schema["properties"]["confidence"]["enum"] == ["high", "medium", "low"]
    assert schema["properties"]["answer"]["maxLength"] == 1200  # sortie bornée


def test_schema_rejects_out_of_contract_output():
    RagAnswer.model_validate({"answer": "ok", "sources": [], "confidence": "low"})
    with pytest.raises(ValueError):
        RagAnswer.model_validate({"answer": "ok", "sources": [], "confidence": "sure"})
