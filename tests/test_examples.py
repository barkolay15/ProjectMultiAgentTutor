"""Публичные примеры формируются без выполнения тестов и пользовательского кода."""
import pytest

from core.examples import example_from_test


@pytest.mark.parametrize("source, expected", [
    ("assert sum_even([1, 2, 3, 4]) == 6", {"input": "[1, 2, 3, 4]", "output": "6"}),
    ("assert count_char('Python', 'o') == 1", {"input": "'Python', 'o'", "output": "1"}),
    ("assert f('', limit=-2) == []", {"input": "'', limit=-2", "output": "[]"}),
    ("assert f() == None", {"input": "Без аргументов", "output": "None"}),
])
def test_public_example(source, expected):
    assert example_from_test(source) == expected


@pytest.mark.parametrize("source", [
    "assert f(dangerous()) == 1", "assert f(1) == dangerous()",
    "assert f(1) > 0", "assert f(1) == 1 == 1", "print('side effect')", "", "bad syntax!",
])
def test_unsupported_example_does_not_execute_code(source):
    assert example_from_test(source) is None
