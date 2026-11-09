"""Профиль и сброс обучения через API, с настоящей SQLite и без LLM."""
import dataclasses
import os
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from copy import deepcopy
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

os.environ["USE_REAL_CHECKER"] = "0"  # До импорта API: тесты не используют модель из локального .env.

from agents.memory.agent import get_profile, update_student_state
from api import routes
from api.main import app
from core.adapters import with_real_memory
from core.stubs import stub_agents
from schemas.messages import MemoryEvent, StudentProfile, TutorReply


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(routes, "_sessions", {})
    monkeypatch.setattr(routes, "_student_locks", {})
    monkeypatch.setattr(routes, "_agents", with_real_memory(stub_agents()))
    return TestClient(app)


def seed(student):
    for i in range(5):
        update_student_state(MemoryEvent(student_id=student, topic="строки", result="pass", task_id=f"bank/{i}"))
    update_student_state(MemoryEvent(student_id=student, topic="циклы", result="fail", error_type="SyntaxError"))
    return get_profile(student).model_dump()


def test_session_restores_progress_before_first_chat(client, monkeypatch):
    expected = seed("restore")
    check, tutor = Mock(), Mock()
    monkeypatch.setattr(routes, "_agents", dataclasses.replace(routes._agents, check=check, tutor=tutor))
    before = client.get("/metrics").json()["sessions"]
    data = client.get("/session/restore").json()
    assert data["task"] is None and data["history"] == [] and data["hint_level"] == 1
    assert data["solved"] == expected["solved_task_ids"]
    assert data["profile"] == expected and data["errors"] == []
    assert "restore" not in routes._sessions
    assert client.get("/metrics").json()["sessions"] == before
    check.assert_not_called()
    tutor.assert_not_called()


def test_session_merges_cached_and_persistent_progress_without_losing_dialog(client):
    expected = seed("existing")
    session = routes._new_session()
    session.update(history=[{"role": "student", "text": "Вопрос"}], hint_level=2,
                   solved=["demo/local", "bank/0"])
    routes._sessions["existing"] = session
    data = client.get("/session/existing").json()
    assert data["history"] == session["history"] and data["hint_level"] == 2
    assert data["solved"] == ["demo/local", *expected["solved_task_ids"]]
    assert session["solved"] == data["solved"]


def test_profile_returns_current_learning_data(client):
    expected = seed("profile")
    response = client.get("/profile/profile")
    assert response.status_code == 200 and response.json() == expected
    assert response.json()["level"] == "intermediate"
    assert response.json()["weak_topics"] == ["циклы"]
    assert response.json()["typical_errors"] == ["SyntaxError"]
    update_student_state(MemoryEvent(student_id="profile", topic="числа", result="pass", task_id="bank/new"))
    assert "bank/new" in client.get("/profile/profile").json()["solved_task_ids"]
    assert client.get("/info").json()["memory"] == "sqlite"


def test_dialog_reset_preserves_profile_and_restores_saved_solutions(client):
    expected = seed("dialog_reset")
    routes._sessions["dialog_reset"] = routes._new_session()
    routes._sessions["dialog_reset"]["history"] = [{"role": "student", "text": "Вопрос"}]
    assert client.post("/reset/dialog_reset").json() == {"reset": "dialog_reset"}
    data = client.get("/session/dialog_reset").json()
    assert data["history"] == [] and data["task"] is None
    assert data["profile"] == expected and data["solved"] == expected["solved_task_ids"]


def test_full_reset_only_clears_target_student_and_is_idempotent(client):
    seed("target")
    other_profile = seed("other")
    for student in ("target", "other"):
        routes._sessions[student] = routes._new_session()
        routes._sessions[student]["history"] = [{"role": "student", "text": student}]
    other_session = deepcopy(routes._sessions["other"])
    for _ in range(2):
        response = client.post("/reset/target/all")
        assert response.status_code == 200 and response.json() == {"reset": "target", "profile_reset": True}
        assert "target" not in routes._sessions
        assert client.get("/profile/target").json() == StudentProfile(student_id="target").model_dump()
        assert client.get("/session/target").json()["solved"] == []
        assert client.get("/profile/other").json() == other_profile
        assert routes._sessions["other"] == other_session


def test_profile_read_failure_keeps_session_available(client, monkeypatch):
    session = routes._new_session()
    session.update(history=[{"role": "student", "text": "Сохранённый вопрос"}], solved=["bank/cached"])
    routes._sessions["unavailable"] = session
    memory = Mock(side_effect=OSError("database offline"))
    monkeypatch.setattr(routes, "_agents", dataclasses.replace(routes._agents, memory_get=memory))
    response = client.get("/session/unavailable")
    assert response.status_code == 200
    data = response.json()
    assert data["profile"] is None and data["solved"] == ["bank/cached"]
    assert data["history"] == session["history"]
    assert any("load_profile: OSError" in error for error in data["errors"])
    assert client.get("/profile/unavailable").status_code == 503


def test_wrong_student_profile_is_not_exposed(client, monkeypatch):
    monkeypatch.setattr(routes, "_agents", dataclasses.replace(
        routes._agents, memory_get=lambda *_: StudentProfile(student_id="someone_else", solved_task_ids=["private"])))
    response = client.get("/profile/requested")
    assert response.status_code == 503 and "private" not in response.text
    data = client.get("/session/requested").json()
    assert data["profile"] is None and data["solved"] == []


def test_failed_full_reset_preserves_session_and_learning_data(client, monkeypatch):
    seed("reset_failure")
    routes._sessions["reset_failure"] = routes._new_session()
    before = client.get("/session/reset_failure").json()
    reset = Mock(side_effect=OSError("database read-only"))
    monkeypatch.setattr(routes, "_agents", dataclasses.replace(routes._agents, memory_reset=reset))
    assert client.post("/reset/reset_failure/all").status_code == 503
    assert client.get("/session/reset_failure").json() == before
    reset.assert_called_once_with("reset_failure")


def test_full_reset_reports_unsupported_provider_without_clearing_session(client, monkeypatch):
    routes._sessions["unsupported"] = routes._new_session()
    monkeypatch.setattr(routes, "_agents", dataclasses.replace(routes._agents, memory_reset=None))
    assert client.post("/reset/unsupported/all").status_code == 501
    assert "unsupported" in routes._sessions


def test_stub_backend_supports_profile_and_reset_without_sqlite(client, monkeypatch):
    from agents.memory import agent

    connect = Mock(side_effect=AssertionError("Заглушка не должна обращаться к SQLite"))
    monkeypatch.setattr(agent, "_connect", connect)
    monkeypatch.setattr(routes, "_agents", stub_agents())
    assert client.get("/info").json()["memory"] == "stub"
    assert client.get("/profile/demo").json() == StudentProfile(student_id="demo").model_dump()
    assert client.post("/reset/demo/all").status_code == 200
    connect.assert_not_called()


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
def test_full_reset_waits_for_turn_and_removes_its_learning_event(client, monkeypatch, path):
    seed("inflight")
    entered, release, reset_started = threading.Event(), threading.Event(), threading.Event()

    def slow_tutor(req, err):
        entered.set()
        assert release.wait(5)
        return TutorReply(message="Готово", hint_level=1)

    monkeypatch.setattr(routes, "_agents", dataclasses.replace(routes._agents, tutor=slow_tutor, checks_are_real=True))

    def reset():
        reset_started.set()
        return client.post("/reset/inflight/all")

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(client.post, path, json={"student_id": "inflight", "message": "check",
                                                      "code": "def strlen(s): return len(s)"})
        try:
            assert entered.wait(5)
            changed = pool.submit(reset)
            assert reset_started.wait(5)
            with pytest.raises(TimeoutError):
                changed.result(timeout=0.2)
            assert client.get("/profile/other").status_code == 200
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 200
        assert changed.result(timeout=5).status_code == 200
    assert client.get("/profile/inflight").json() == StudentProfile(student_id="inflight").model_dump()
    assert client.get("/session/inflight").json()["history"] == []


@pytest.mark.parametrize("method, path", [
    ("GET", "/profile/bad id"), ("GET", "/session/bad id"),
    ("POST", "/reset/bad id"), ("POST", "/reset/bad id/all"),
])
def test_student_id_validation_is_consistent(client, method, path):
    assert client.request(method, path).status_code == 422
