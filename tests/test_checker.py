"""Запуск из корня проекта: python3 -m tests.test_checker"""
from agents.checker.sandbox import check
from schemas.messages import CheckRequest


def v(task_id, code):
    return check(CheckRequest(task_id=task_id, student_code=code))


def test_correct():
    r = v("sample/sum_even", "def sum_even(numbers):\n    return sum(x for x in numbers if x % 2 == 0)")
    assert r.passed and r.failed_tests == []


def test_wrong_answer_lists_failed_tests():
    r = v("sample/sum_even", "def sum_even(numbers):\n    return sum(numbers)")
    assert not r.passed and r.error_type == "AssertionError"
    assert "assert sum_even([1, 2, 3, 4]) == 6" in r.failed_tests


def test_failed_details_contain_actual_result():
    r = v("sample/sum_even", "def sum_even(numbers):\n    return sum(numbers)")
    got = {d.test: d.got for d in r.failed_details}
    assert got["assert sum_even([1, 2, 3, 4]) == 6"] == "10"
    assert got["assert sum_even([1, 3, 5]) == 0"] == "9"


def test_syntax_error():
    r = v("sample/sum_even", "def sum_even(numbers)\n    return 0")
    assert not r.passed and r.error_type == "SyntaxError"


def test_runtime_error_in_tests():
    r = v("sample/reverse_words", "def reverse_words(s):\n    return s.nope()")
    assert not r.passed and r.error_type == "AttributeError"


def test_infinite_loop_times_out():
    r = v("sample/sum_even", "def sum_even(numbers):\n    while True:\n        pass")
    assert not r.passed and r.error_type in ("Timeout", "RuntimeCrash")


def test_print_does_not_break_verdict():
    r = v("sample/sum_even",
          "print('@@RESULT@@ hello')\ndef sum_even(n):\n    return sum(x for x in n if x % 2 == 0)")
    assert r.passed


def test_unknown_task():
    r = v("nope/1", "x = 1")
    assert not r.passed and r.error_type == "UnknownTask"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
