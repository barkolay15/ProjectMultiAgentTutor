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


def test_chat_stream_sends_steps_then_result():
    import json
    r = client.post("/chat/stream", json={"student_id": "api_s", "message": "проверьте", "code": BAD})
    assert r.status_code == 200
    events = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    steps = [e["node"] for e in events if e["type"] == "step"]
    assert steps[:4] == ["load_profile", "pick_task", "check", "review"]
    assert events[-1]["type"] == "result"
    data = events[-1]["data"]
    assert data["verdict"]["passed"] is False and data["status"] == "ok"
    assert client.get("/session/api_s").json()["hint_level"] == 2     # итог сохранён, как у /chat


def test_chat_stream_validates_input():
    assert client.post("/chat/stream", json={"student_id": "x", "message": ""}).status_code == 422


def test_task_bank_endpoints():
    tasks = client.get("/tasks").json()
    assert tasks and {"task_id", "entry_point", "statement"} <= set(tasks[0])
    assert all("tests" not in t for t in tasks)          # тесты студенту не отдаём
    assert all(t["starter"].startswith("def ") and "pass" in t["starter"] for t in tasks)   # заготовка функции
    assert not any(t["task_id"].startswith("stub/") for t in tasks)                        # служебные задачи скрыты
    tid = tasks[0]["task_id"]
    r = client.post("/session/api_g/task", json={"task_id": tid})
    assert r.status_code == 200 and r.json()["task"]["task_id"] == tid
    assert r.json()["starter"] == tasks[0]["starter"]
    sess = client.get("/session/api_g").json()
    assert sess["task"]["task_id"] == tid and sess["hint_level"] == 1 and sess["solved"] == []
    assert client.post("/session/api_g/task", json={"task_id": "no/such"}).status_code == 404


def test_demo_is_off_by_default_and_on_with_flag():
    os.environ.pop("TUTOR_DEMO", None)
    assert client.get("/info").json()["demo"] is False
    assert client.get("/demo/mbpp/99").status_code == 404
    os.environ["TUTOR_DEMO"] = "1"
    try:
        assert client.get("/info").json()["demo"] is True
        assert client.get("/demo/no/such").status_code == 404
        from api import bank
        known = next(iter(bank._demo()), None)
        if known:                                          # id вида mbpp/99 содержит слэш
            data = client.get(f"/demo/{known}").json()
            assert data["reference"] and isinstance(data["mutants"], list)
    finally:
        os.environ.pop("TUTOR_DEMO", None)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)