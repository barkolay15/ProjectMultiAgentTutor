"""Адаптер проверяющего: оркестратор + настоящий agents.checker с поддельной моделью (без сети).
Запуск из корня проекта: python3 -m tests.test_checker_adapter
"""
from core.adapters import with_real_checker
from core.orchestrator import run_turn
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
    assert out["next_task"] is None


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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
