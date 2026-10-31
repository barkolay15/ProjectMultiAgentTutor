"""Запуск из корня проекта: python3 -m tests.test_eval_checker  (ключ и сеть не нужны)"""
import json

from langchain_core.messages import AIMessage

from agents.checker.sandbox import load_tasks
from benchmarks.eval_checker import baseline_category, evaluate, summarize
from schemas.messages import CheckVerdict

ITEMS = [{
    "task_id": "sample/sum_even",
    "reference": "def sum_even(numbers):\n    return sum(x for x in numbers if x % 2 == 0)",
    "mutants": [
        {"variant_id": "m1", "bug_type": "logic", "code": "def sum_even(numbers):\n    return sum(numbers)"},
        {"variant_id": "m2", "bug_type": "syntax", "code": "def sum_even(numbers)\n    return 0"},
        {"variant_id": "m3", "bug_type": "timeout", "code": "def sum_even(numbers):\n    while True:\n        pass"},
        {"variant_id": "m4", "bug_type": "edge_case",
         "code": "def sum_even(numbers):\n    return sum(x for x in numbers if x % 2 == 0 and x > 0)"},
    ],
}]


class FakeLLM:
    """Всегда вызывает run_tests, затем отвечает category = logic."""
    def __init__(self):
        self.step = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.step += 1
        if self.step % 2 == 1:
            return AIMessage(content="", tool_calls=[{"name": "run_tests", "args": {}, "id": f"c{self.step}"}])
        return AIMessage(content=json.dumps({"task_id": "sample/sum_even", "category": "logic",
                                             "summary": "Считает все числа.", "hint_for_tutor": "Почему сумма такая?"},
                                            ensure_ascii=False))


def test_baseline_rules():
    mk = lambda et: CheckVerdict(task_id="t", passed=False, error_type=et)
    assert baseline_category(mk("SyntaxError")) == "syntax"
    assert baseline_category(mk("Timeout")) == "timeout"
    assert baseline_category(mk("AssertionError")) == "logic"
    assert baseline_category(mk("AttributeError")) == "runtime"


def test_baseline_run_and_summary():
    rows = evaluate(ITEMS, load_tasks(), mode="baseline", log=lambda *_: None)
    assert [r["predicted"] for r in rows] == ["logic", "syntax", "timeout", "logic"]
    s = summarize(rows)
    assert s["category_accuracy"] == 0.75           # edge_case правилом не узнать
    assert s["accuracy_by_type"]["edge_case"] == 0.0


def test_llm_run_collects_stats():
    rows = evaluate(ITEMS[:1], load_tasks(), mode="llm", llm=FakeLLM(), log=lambda *_: None)
    r = rows[0]
    assert r["tool_called_by_model"] is True and r["json_valid_first_try"] is True
    assert r["no_code_first_try"] is True and r["analysis_ok"] is True and r["llm_calls"] == 2


def test_errors_do_not_stop_the_run():
    class Broken:
        def bind_tools(self, tools):
            raise RuntimeError("сеть упала")
    rows = evaluate(ITEMS, load_tasks(), mode="llm", llm=Broken(), log=lambda *_: None)
    assert len(rows) == 4 and all(r["error"] for r in rows)
    assert summarize(rows)["n_errors"] == 4


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
