"""Тесты агента с поддельной моделью (ключ и сеть не нужны).
Запуск из корня проекта: python3 -m tests.test_checker_agent
"""
import json

from langchain_core.messages import AIMessage

from agents.checker.agent import analyze, looks_like_solution
from schemas.messages import CheckRequest

WRONG = "def sum_even(numbers):\n    return sum(numbers)"
RIGHT = "def sum_even(numbers):\n    return sum(x for x in numbers if x % 2 == 0)"
REQ = lambda code: CheckRequest(task_id="sample/sum_even", student_code=code)


def tool_call():
    return AIMessage(content="", tool_calls=[
        {"name": "run_tests", "args": {"task_id": "sample/sum_even", "student_code": "ignored"}, "id": "c1"}])


def analysis(summary="Код суммирует все числа, а нужны только чётные.",
             hint="Спросите, какие элементы списка должны попадать в сумму."):
    return AIMessage(content=json.dumps({
        "task_id": "sample/sum_even", "category": "logic",
        "summary": summary, "hint_for_tutor": hint}, ensure_ascii=False))


class FakeLLM:
    """Отдаёт заранее заданные ответы по очереди и считает вызовы."""
    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.calls += 1
        return self.replies.pop(0)


def test_wrong_code_gets_analysis():
    llm = FakeLLM([tool_call(), analysis()])
    verdict, ana = analyze(REQ(WRONG), llm)
    assert not verdict.passed and ana is not None and ana.category == "logic"


def test_passed_code_skips_second_llm_call():
    llm = FakeLLM([tool_call()])
    verdict, ana = analyze(REQ(RIGHT), llm)
    assert verdict.passed and ana is None and llm.calls == 1


def test_model_cannot_replace_student_code():
    # модель передала в инструмент "ignored", но тесты запускаются на коде студента
    llm = FakeLLM([tool_call()])
    verdict, _ = analyze(REQ(RIGHT), llm)
    assert verdict.passed


def test_invalid_json_is_retried():
    llm = FakeLLM([tool_call(), AIMessage(content="вот ответ: не json"), analysis()])
    verdict, ana = analyze(REQ(WRONG), llm)
    assert ana is not None and llm.calls == 3


def test_solution_in_hint_is_rejected_then_fixed():
    leaked = analysis(hint="Замените на:\n```python\nreturn sum(x for x in numbers if x % 2 == 0)\n```")
    llm = FakeLLM([tool_call(), leaked, analysis()])
    verdict, ana = analyze(REQ(WRONG), llm)
    assert ana is not None and not looks_like_solution(ana.hint_for_tutor)


def test_gives_up_after_retries_but_keeps_verdict():
    bad = AIMessage(content="не json")
    llm = FakeLLM([tool_call(), bad, bad, bad])
    verdict, ana = analyze(REQ(WRONG), llm)
    assert not verdict.passed and ana is None


def test_model_answers_without_tool_still_gets_verdict():
    llm = FakeLLM([analysis(), analysis()])
    verdict, ana = analyze(REQ(WRONG), llm)
    assert not verdict.passed and ana is not None


def test_stats_are_filled():
    st = {}
    analyze(REQ(WRONG), FakeLLM([tool_call(), analysis()]), st)
    assert st["tool_called_by_model"] and st["json_valid_first_try"] and st["no_code_first_try"]
    assert st["retries"] == 0 and st["llm_calls"] == 2


def test_stats_show_bad_first_answer_and_retry():
    st = {}
    analyze(REQ(WRONG), FakeLLM([tool_call(), AIMessage(content="не json"), analysis()]), st)
    assert st["json_valid_first_try"] is False and st["retries"] == 1 and st["llm_calls"] == 3


def test_stats_when_model_skips_tool():
    st = {}
    analyze(REQ(WRONG), FakeLLM([analysis(), analysis()]), st)
    assert st["tool_called_by_model"] is False


def test_looks_like_solution():
    assert looks_like_solution("return x + 1")
    assert looks_like_solution("```py\nprint(1)\n```")
    assert not looks_like_solution("Код суммирует все числа, а нужны только чётные.")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
