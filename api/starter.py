"""Заготовка кода для задачи: строка `def имя(аргументы):` и пустое тело, как в LeetCode.

Откуда берётся сигнатура (первое подходящее):
  1. поле `starter` в самой задаче;
  2. эталонное решение: из него берётся ТОЛЬКО строка def, тело не используется;
  3. запись вида `имя(аргументы)` в описании задачи;
  4. число аргументов в первом тесте (имена arg1, arg2, ...).
Без внешних библиотек, поэтому модуль легко тестируется.
"""
import ast
import re
import warnings

BODY = "    # Напишите решение здесь\n    pass\n"
_ARG = re.compile(r"^\*{0,2}[A-Za-z_]\w*(\s*=\s*[^,]+)?$")


def _parse(source: str):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")        # строки вроде '\\w' в тестах дают SyntaxWarning
        return ast.parse(source)


def from_source(entry_point: str, source: str | None) -> str | None:
    try:
        tree = _parse(source or "")
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == entry_point:
            return f"def {entry_point}({ast.unparse(node.args)}):"
    return None


def from_statement(entry_point: str, statement: str | None) -> str | None:
    text = (statement or "").split("Пример проверки")[0]
    for m in re.finditer(rf"\b{re.escape(entry_point)}\(([^()]*)\)", text):
        parts = [p.strip() for p in m.group(1).split(",")] if m.group(1).strip() else []
        if all(_ARG.match(p) for p in parts):
            return f"def {entry_point}({', '.join(parts)}):"
    return None


def from_tests(entry_point: str, tests: list[str] | None) -> str | None:
    for test in tests or []:
        try:
            tree = _parse(test)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == entry_point:
                names = ", ".join(f"arg{i + 1}" for i in range(len(node.args)))
                return f"def {entry_point}({names}):"
    return None


def make_starter(task: dict, reference: str | None = None) -> str:
    if task.get("starter"):
        return task["starter"]
    entry_point = task.get("entry_point") or ""
    if not entry_point:
        return ""
    line = (from_source(entry_point, reference)
            or from_statement(entry_point, task.get("statement"))
            or from_tests(entry_point, task.get("tests"))
            or f"def {entry_point}():")
    return f"{line}\n{BODY}"