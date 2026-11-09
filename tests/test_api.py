"""Тесты HTTP-ручек: настоящий FastAPI на заглушках, ключ и сеть не нужны.
Запуск из корня проекта: python -m pytest tests/test_api.py (нужен httpx)
"""
import os

os.environ["USE_REAL_CHECKER"] = "0"   # до импорта api: .env не должен включать настоящего проверяющего

import dataclasses
import json
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import pytest
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
    from agents.checker.prompts import PROMPT_VERSION
    from core.llm_client import MODEL_NAME
    assert info["checker_prompt_version"] == PROMPT_VERSION
    assert info["model"] == (MODEL_NAME if info["tutor"] == "llm" else None)


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


def test_right_code_keeps_solved_task_open():
    data = post("api_c", code=GOOD).json()
    assert data["verdict"]["passed"] is True
    sess = client.get("/session/api_c").json()
    assert sess["task"]["task_id"] == data["task"]["task_id"]
    assert data["task"]["task_id"] in sess["solved"]


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
    assert all(t.get("example") and {"input", "output"} == set(t["example"]) for t in tasks)
    sample = next(t for t in tasks if t["task_id"] == "sample/sum_even")
    assert sample["example"] == {"input": "[1, 2, 3, 4]", "output": "6"}
    assert all(t["starter"].startswith("def ") and "pass" in t["starter"] for t in tasks)   # заготовка функции
    assert not any(t["task_id"].startswith("stub/") for t in tasks)                        # служебные задачи скрыты
    tid = tasks[0]["task_id"]
    r = client.post("/session/api_g/task", json={"task_id": tid})
    assert r.status_code == 200 and r.json()["task"]["task_id"] == tid
    assert r.json()["starter"] == tasks[0]["starter"]
    assert r.json()["task"]["example"] == tasks[0]["example"]
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


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
def test_recheck_and_followup_stay_on_solved_task(monkeypatch, path):
    from api import bank

    monkeypatch.setattr(routes, "_agents", dataclasses.replace(
        stub_agents(), tutor=fake_tutor, next_task=routes._bank_next_task))
    student = "api_recheck_" + path.rsplit("/", 1)[-1]
    task = bank.all_tasks()[0]
    client.post(f"/reset/{student}")
    client.post(f"/session/{student}/task", json={"task_id": task.task_id})

    def turn(message, code):
        response = client.post(path, json={"student_id": student, "message": message,
                                          "code": code, "task_id": task.task_id})
        assert response.status_code == 200
        if path == "/chat":
            return response.json()
        return json.loads(response.text.splitlines()[-1])["data"]

    for data in [turn("проверьте", GOOD), turn("проверьте ещё раз", GOOD), turn("Почему это работает?", None)]:
        assert data["status"] == "ok"
        assert data["task"]["task_id"] == task.task_id
        assert data["solved"] == [task.task_id]
    sess = client.get(f"/session/{student}").json()
    assert sess["task"]["task_id"] == task.task_id
    assert sess["history"][0]["code"] == GOOD


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
def test_stale_task_is_rejected_without_running_agents(monkeypatch, path):
    from api import bank
    from unittest.mock import Mock

    student = "api_stale_" + path.rsplit("/", 1)[-1]
    current, stale = bank.all_tasks()[:2]
    client.post(f"/session/{student}/task", json={"task_id": current.task_id})
    check = Mock(side_effect=AssertionError("Старый код нельзя проверять на другой задаче"))
    monkeypatch.setattr(routes, "_agents", dataclasses.replace(stub_agents(), check=check))
    before = client.get(f"/session/{student}").json()
    response = client.post(path, json={"student_id": student, "message": "проверьте", "code": GOOD,
                                       "task_id": stale.task_id})
    if path == "/chat":
        assert response.status_code == 409
    else:
        event = json.loads(response.text.splitlines()[-1])
        assert event["type"] == "error" and event["status"] == 409
    check.assert_not_called()
    assert client.get(f"/session/{student}").json() == before


@pytest.mark.parametrize("path", ["/chat", "/chat/stream"])
@pytest.mark.parametrize("mutation", ["choose", "reset"])
def test_session_changes_wait_for_inflight_turn(monkeypatch, path, mutation):
    from api import bank

    first, second = bank.all_tasks()[:2]
    student = f"api_concurrent_{mutation}_{path.rsplit('/', 1)[-1]}"
    client.post(f"/session/{student}/task", json={"task_id": first.task_id})
    entered, release, mutation_started = threading.Event(), threading.Event(), threading.Event()

    def slow_tutor(req, err):
        entered.set()
        assert release.wait(5)
        return fake_tutor(req, err)

    monkeypatch.setattr(routes, "_agents", dataclasses.replace(stub_agents(), tutor=slow_tutor))

    def mutate():
        mutation_started.set()
        if mutation == "choose":
            return client.post(f"/session/{student}/task", json={"task_id": second.task_id})
        return client.post(f"/reset/{student}")

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(client.post, path, json={"student_id": student, "message": "подсказка"})
        try:
            assert entered.wait(5)
            changed = pool.submit(mutate)
            assert mutation_started.wait(5)
            with pytest.raises(TimeoutError):
                changed.result(timeout=0.2)
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 200
        assert changed.result(timeout=5).status_code == 200
    sess = client.get(f"/session/{student}").json()
    if mutation == "choose":
        assert sess["task"]["task_id"] == second.task_id and sess["hint_level"] == 1
    else:
        assert sess["task"] is None and sess["history"] == [] and sess["solved"] == []


def test_next_task_uses_persistent_progress_after_session_reset(monkeypatch):
    from api import bank
    from core.adapters import with_real_memory

    # Доверенный поддельный проверяющий позволяет проверить интеграцию без модели и сети.
    agents = with_real_memory(dataclasses.replace(stub_agents(), tutor=fake_tutor,
                              next_task=routes._bank_next_task, checks_are_real=True))
    monkeypatch.setattr(routes, "_agents", agents)
    student = "api_persistent_progress"
    task = bank.all_tasks()[0]
    client.post(f"/session/{student}/task", json={"task_id": task.task_id})
    passed = post(student, code=GOOD).json()
    assert passed["verdict"]["passed"]
    client.post(f"/reset/{student}")
    resumed = post(student, message="предложи задачу").json()
    assert resumed["status"] == "ok"
    assert resumed["task"]["task_id"] != task.task_id
    assert task.task_id in resumed["solved"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
