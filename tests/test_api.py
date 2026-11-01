"""Тесты HTTP-ручек: настоящий FastAPI на заглушках, ключ и сеть не нужны.
Запуск из корня проекта: python -m pytest tests/test_api.py (нужен httpx)
"""
import os

os.environ["USE_REAL_CHECKER"] = "0"   # до импорта api: .env не должен включать настоящего проверяющего

import dataclasses

from fastapi.testclient import TestClient

from api import routes
from api.main import app
from core.stubs import stub_agents
from schemas.messages import TutorReply

GOOD = "def strlen(s): return len(s)"
BAD = "def strlen(s): return 0"


def fake_tutor(req, err):   # настоящий тьютор не должен ходить в сеть из тестов
    return TutorReply(message="Подумайте ещё раз.", hint_level=req.hint_level).model_dump_json()


routes.set_agents(dataclasses.replace(stub_agents(), tutor=fake_tutor))
client = TestClient(app)


def post(student, message="проверьте", code=None):
    return client.post("/chat", json={"student_id": student, "message": message, "code": code})


def test_health_and_info():
    assert client.get("/health").json() == {"status": "ok"}
    info = client.get("/info").json()
    assert info["checker"] == "stub" and info["tutor"] in ("stub", "llm") and info["max_steps"] > 0


def test_chat_without_code_gives_task_but_no_verdict():
    r = post("api_a", "Не понимаю задачу")
    assert r.status_code == 200
    data = r.json()
    assert data["reply"] and data["task"] and data["verdict"] is None and data["status"] == "ok"


def test_wrong_code_raises_hint_and_is_saved_in_session():
    data = post("api_b", code=BAD).json()
    assert data["verdict"]["passed"] is False and data["analysis"] is not None
    sess = client.get("/session/api_b").json()
    assert sess["hint_level"] == 2 and sess["task"] is not None
    assert [h["role"] for h in sess["history"]] == ["student", "tutor"]


def test_right_code_closes_task():
    data = post("api_c", code=GOOD).json()
    assert data["verdict"]["passed"] is True
    assert client.get("/session/api_c").json()["task"] is None


def test_validation_errors():
    assert client.post("/chat", json={"student_id": "x", "message": ""}).status_code == 422
    assert client.post("/chat", json={"student_id": "x", "message": "   "}).status_code == 422
    assert client.post("/chat", json={"student_id": "", "message": "привет"}).status_code == 422
    assert client.post("/chat", json={"student_id": "a b", "message": "привет"}).status_code == 422
    assert client.post("/chat", json={"student_id": "x", "message": "я" * 2001}).status_code == 422


def test_blank_code_is_treated_as_no_code():
    data = post("api_d", code="   ").json()
    assert data["verdict"] is None


def test_unknown_session_and_reset():
    empty = client.get("/session/nobody").json()
    assert empty["history"] == [] and empty["task"] is None and empty["hint_level"] == 1
    post("api_e", "привет")
    assert client.post("/reset/api_e").json() == {"reset": "api_e"}
    assert client.get("/session/api_e").json()["history"] == []


def test_metrics_count_turns():
    before = client.get("/metrics").json()["sessions"]
    post("api_f", "привет")
    assert client.get("/metrics").json()["sessions"] == before + 1


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
