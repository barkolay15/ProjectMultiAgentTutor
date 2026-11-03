"""Подключение настоящих агентов к оркестратору.

Проверяющий (D) имеет одну функцию analyze(req, llm, stats=None) -> (verdict, analysis | None):
она сама запускает run_tests, сама повторяет невалидный JSON и при неудаче возвращает analysis=None.
Оркестратор же вызывает проверку и ревью двумя узлами, поэтому адаптер вызывает analyze один раз
в узле check, а разбор отдаёт узлу review из кэша.
"""
import dataclasses

from core.orchestrator import Agents


def with_real_checker(agents: Agents, llm) -> Agents:
    from agents.checker.agent import analyze as checker_analyze

    cache: dict[tuple[str, str], tuple[object, str | None]] = {}

    def check(req, err):
        stats = {}
        verdict, analysis = checker_analyze(req, llm, stats=stats)
        if not verdict.passed:
            cache[(req.task_id, req.student_code)] = (analysis, stats.get("llm_error"))
        return verdict

    def analyze(payload, err):
        verdict, code = payload["verdict"], payload["student_code"]
        analysis, llm_error = cache.pop((verdict.task_id, code), (None, None))
        if analysis is None:
            if llm_error:
                raise ValueError(f"разбор ошибки недоступен: {llm_error}")
            raise ValueError("проверяющий не вернул разбор ошибки")
        return analysis

    return dataclasses.replace(agents, check=check, analyze=analyze)
