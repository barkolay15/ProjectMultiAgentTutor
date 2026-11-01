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

    cache: dict[tuple[str, str], object] = {}   # (task_id, код студента) -> ErrorAnalysis | None

    def check(req, err):
        verdict, analysis = checker_analyze(req, llm)
        cache[(req.task_id, req.student_code)] = analysis
        return verdict

    def analyze(payload, err):
        verdict, code = payload["verdict"], payload["student_code"]
        analysis = cache.pop((verdict.task_id, code), None)
        if analysis is None:
            raise ValueError("проверяющий не вернул разбор ошибки")
        return analysis

    return dataclasses.replace(agents, check=check, analyze=analyze)