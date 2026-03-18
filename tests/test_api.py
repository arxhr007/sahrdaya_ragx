import os

os.environ.setdefault("GROQ_API_KEYS", "test-key")

from fastapi.testclient import TestClient

from api.app import app
from api.core.models import ChatResponse
from api.routes import chat as chat_routes


client = TestClient(app)


def _reset_session_store():
    chat_routes.session_store._sessions.clear()


def test_health_endpoint():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_session_lifecycle():
    _reset_session_store()

    create_res = client.post("/api/sessions")
    assert create_res.status_code == 200
    session_id = create_res.json()["session_id"]

    hist_res = client.get(f"/api/sessions/{session_id}/history")
    assert hist_res.status_code == 200
    assert hist_res.json()["turns"] == []

    del_res = client.delete(f"/api/sessions/{session_id}")
    assert del_res.status_code == 204

    hist_res2 = client.get(f"/api/sessions/{session_id}/history")
    assert hist_res2.status_code == 404


def test_chat_sql_path(monkeypatch):
    _reset_session_store()

    async def fake_classify_sql(_question, _history):
        return "SELECT name FROM faculty", "test-key"

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("RAG path should not run for SQL test")

    monkeypatch.setattr(chat_routes, "_classify_sql", fake_classify_sql)
    monkeypatch.setattr(chat_routes, "_answer_rag", fail_if_called)
    monkeypatch.setattr(chat_routes.rag_setup, "validate_faculty_sql", lambda _sql: True)
    monkeypatch.setattr(
        chat_routes.rag_setup,
        "execute_faculty_sql",
        lambda _sql: (["name"], [("Dr. Test",)]),
    )
    monkeypatch.setattr(
        chat_routes.rag_setup,
        "format_sql_results",
        lambda _c, _r, _q: "SQL_RESULT",
    )
    monkeypatch.setattr(chat_routes.rate_manager, "can_consume", lambda _t: (True, 0.0))
    monkeypatch.setattr(chat_routes.rate_manager, "consume", lambda _t: None)

    payload = {"message": "list all cse faculty"}
    response = client.post("/api/chat", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "SQL_RESULT"
    assert body["metadata"]["mode"] == "sql"


def test_chat_rag_path(monkeypatch):
    _reset_session_store()

    async def fake_classify_sql(_question, _history):
        return None, "test-key"

    async def fake_answer_rag(_question, _history, _context):
        return "RAG_FULL_ANSWER", "test-key"

    monkeypatch.setattr(chat_routes, "_classify_sql", fake_classify_sql)
    monkeypatch.setattr(chat_routes, "_answer_rag", fake_answer_rag)
    monkeypatch.setattr(
        chat_routes.rag_setup,
        "retrieve_with_metadata",
        lambda _q: ("context", ["chunk_1"], 1),
    )
    monkeypatch.setattr(chat_routes.rate_manager, "can_consume", lambda _t: (True, 0.0))
    monkeypatch.setattr(chat_routes.rate_manager, "consume", lambda _t: None)

    payload = {"message": "who is principal"}
    response = client.post("/api/chat", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "RAG_FULL_ANSWER"
    assert body["metadata"]["mode"] == "rag"
    assert body["metadata"]["chunk_ids"] == ["chunk_1"]


def test_chat_stream_returns_events(monkeypatch):
    _reset_session_store()

    async def fake_process_chat(_req, session_id):
        return ChatResponse(
            session_id=session_id,
            answer="Hello world streaming",
            metadata=None,
        )

    monkeypatch.setattr(chat_routes, "_process_chat", fake_process_chat)

    response = client.post("/api/chat/stream", json={"message": "hi"})
    assert response.status_code == 200
    assert "event: started" in response.text
    assert "event: token" in response.text
    assert "event: completed" in response.text
