"""Тесты заготовок кода (без сети и без библиотек).
Запуск из корня проекта: python -m tests.test_starter (или pytest)
"""
from api.starter import from_source, from_statement, from_tests, make_starter


def test_signature_comes_from_reference_but_body_does_not():
    ref = "def count_char(string, char):\n    return string.count(char)\n"
    code = make_starter({"entry_point": "count_char", "statement": "x"}, ref)
    assert code.startswith("def count_char(string, char):\n")
    assert "count(" not in code and "pass" in code          # тело эталона наружу не попадает
    compile(code, "starter", "exec")


def test_helper_function_before_entry_point_is_skipped():
    ref = "def helper(a):\n    return a\ndef main_fn(x, y=2):\n    return helper(x)\n"
    assert from_source("main_fn", ref) == "def main_fn(x, y=2):"
    assert from_source("нет", ref) is None
    assert from_source("f", "def f(:") is None             # битый эталон не ломает заготовку


def test_signature_from_statement_ignores_example_calls():
    assert from_statement("sum_even", "Напишите функцию sum_even(numbers), которая...") == "def sum_even(numbers):"
    assert from_statement("f", "Функция f(1, 2) складывает.") is None
    assert from_statement("f", "Сделайте f(a, b=1). Пример проверки: assert f(3) == 3") == "def f(a, b=1):"


def test_signature_from_tests_counts_arguments():
    assert from_tests("f", ["assert f(1, 2) == 3"]) == "def f(arg1, arg2):"
    assert from_tests("f", ["assert 1 == 1", "assert f() == 0"]) == "def f():"
    assert from_tests("f", ["не код", "assert g(1) == 1"]) is None


def test_fallbacks_and_explicit_starter():
    assert make_starter({"entry_point": "g", "statement": "?", "tests": ["assert g(5) == 5"]}).startswith("def g(arg1):")
    assert make_starter({"entry_point": "h"}).startswith("def h():")
    assert make_starter({}) == ""
    assert make_starter({"entry_point": "k", "starter": "def k(x):\n    ...\n"}) == "def k(x):\n    ...\n"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)