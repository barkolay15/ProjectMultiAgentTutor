"""Публичный пример входа и результата из проверки, без выполнения кода."""
import ast
import warnings


def example_from_test(source: str) -> dict[str, str] | None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            body = ast.parse(source).body
        if len(body) != 1 or not isinstance(body[0], ast.Assert):
            return None
        comparison = body[0].test
        if not (isinstance(comparison, ast.Compare) and len(comparison.ops) == 1
                and isinstance(comparison.ops[0], ast.Eq) and isinstance(comparison.left, ast.Call)):
            return None
        call = comparison.left
        arguments = [repr(ast.literal_eval(arg)) for arg in call.args]
        for keyword in call.keywords:
            if keyword.arg is None:
                return None
            arguments.append(f"{keyword.arg}={ast.literal_eval(keyword.value)!r}")
        expected = ast.literal_eval(comparison.comparators[0])
        return {"input": ", ".join(arguments) if arguments else "Без аргументов", "output": repr(expected)}
    except (SyntaxError, ValueError, TypeError):
        return None
