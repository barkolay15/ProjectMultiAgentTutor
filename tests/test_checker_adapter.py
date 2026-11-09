"""Адаптер проверяющего: оркестратор + настоящий agents.checker с поддельной моделью (без сети).
Запуск из корня проекта: python3 -m tests.test_checker_adapter
"""
from core.adapters import with_real_checker, with_real_memory
from core.orchestrator import run_turn, stream_turn
from core.stubs import stub_agents
from tests.test_checker_agent import RIGHT, WRONG, FakeLLM, analysis, tool_call
from langchain_core.messages import AIMessage
from schemas.messages import Task
import pytest
from unittest.mock import Mock
from httpx import ConnectTimeout


@pytest.fixture(autouse=True)
def no_live_tutor(monkeypatch):
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "")


def agents_with(llm):
    return with_real_checker(stub_agents(), llm)


TASK = Task(task_id="sample/sum_even", statement="Сумма чётных чисел.", topic="циклы")


def test_wrong_code_goes_through_real_checker_and_review():
    out = run_turn("s1", "проверьте", code=WRONG, task=TASK, agents=agents_with(FakeLLM([tool_call(), analysis()])))
    assert out["status"] == "ok"
    assert out["verdict"].passed is False
    assert out["analysis"].category == "logic"
    assert out["next_hint_level"] == 2


def test_right_code_needs_no_review():
    out = run_turn("s1", "проверьте", code=RIGHT, task=TASK, agents=agents_with(FakeLLM([tool_call()])))
    assert out["verdict"].passed and out.get("analysis") is None
    assert out["next_task"] == TASK


def test_checker_gave_up_tutor_still_answers():
    bad = AIMessage(content="не json")
    out = run_turn("s1", "проверьте", code=WRONG, task=TASK, agents=agents_with(FakeLLM([tool_call(), bad, bad, bad])))
    assert out["status"] == "ok" and out.get("analysis") is None
    assert out["reply"] is not None and out["errors"]


def test_network_failure_keeps_verdict_and_reports_unavailable_analysis():
    llm = Mock()
    llm.bind_tools.return_value = llm
    llm.invoke.side_effect = ConnectTimeout("model unavailable")
    out = run_turn("offline", "проверьте", code=WRONG, task=TASK, agents=agents_with(llm))
    assert out["status"] == "ok"
    assert out["verdict"].passed is False
    assert out.get("analysis") is None
    assert any("ConnectTimeout" in error for error in out["errors"])


def test_identical_parallel_submissions_keep_their_own_analysis(monkeypatch):
    import dataclasses
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from agents.checker import agent as checker
    from schemas.messages import CheckVerdict, ErrorAnalysis

    ready = threading.Barrier(2)
    local = threading.local()

    def fake_checker(req, llm, stats):
        label = local.student
        return (CheckVerdict(task_id=req.task_id, passed=False),
                ErrorAnalysis(task_id=req.task_id, category="logic", summary=label, hint_for_tutor="hint"))

    monkeypatch.setattr(checker, "analyze", fake_checker)
    agents = with_real_checker(stub_agents(), Mock())
    original_check = agents.check

    def check(req, err):
        verdict = original_check(req, err)
        ready.wait(timeout=5)  # Оба check закончились до того, как начнётся review.
        return verdict

    agents = dataclasses.replace(agents, check=check)

    def turn(student):
        local.student = student
        return run_turn(student, "проверьте", code=WRONG, task=TASK, agents=agents)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(turn, ["student_one", "student_two"]))
    assert [out["analysis"].summary for out in results] == ["student_one", "student_two"]
    assert all(not out["errors"] for out in results)
    assert all("_analysis" not in out["verdict"].model_dump() for out in results)


def test_stub_checker_does_not_train_real_memory():
    from agents.memory.agent import get_profile

    agents = with_real_memory(stub_agents())
    for code in (WRONG, "def strlen(s): return len(s)"):
        out = run_turn("demo_memory", "check", code=code, agents=agents)
        assert out["status"] == "ok" and out["memory_event"].source == "stub"
    profile = get_profile("demo_memory")
    assert profile.level == "beginner" and profile.weak_topics == []
    assert profile.typical_errors == [] and profile.solved_task_ids == []


@pytest.mark.parametrize("memory_first", [True, False])
def test_real_checker_records_task_once_in_both_adapter_orders(memory_first):
    from agents.memory.agent import get_profile

    model = FakeLLM([tool_call(), tool_call()])
    if memory_first:
        agents = with_real_checker(with_real_memory(stub_agents()), model)
    else:
        agents = with_real_memory(with_real_checker(stub_agents(), model))
    first = run_turn("real_memory", "check", code=RIGHT, task=TASK, agents=agents)
    second = list(stream_turn("real_memory", "check again", code=RIGHT, task=TASK, agents=agents))[-1]["state"]
    assert first["verdict"].passed and second["verdict"].passed
    assert first["memory_event"].source == second["memory_event"].source == "real"
    profile = get_profile("real_memory")
    assert profile.level == "beginner" and profile.solved_task_ids == [TASK.task_id]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
