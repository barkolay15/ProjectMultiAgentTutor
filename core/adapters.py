"""Подключение настоящих агентов к оркестратору.

Проверяющий (D) имеет одну функцию analyze(req, llm, stats=None) -> (verdict, analysis | None):
она сама запускает run_tests, сама повторяет невалидный JSON и при неудаче возвращает analysis=None.
Оркестратор же вызывает проверку и ревью двумя узлами, поэтому адаптер вызывает analyze один раз
в узле check, а разбор хранит приватно в объекте вердикта этого хода.
"""
import dataclasses

from pydantic import PrivateAttr

from core.orchestrator import Agents
from schemas.messages import CheckVerdict, ErrorAnalysis


class _CheckerVerdict(CheckVerdict):
    """Данные одного хода: приватные поля не попадают в ответ API."""

    _analysis: ErrorAnalysis | None = PrivateAttr(default=None)
    _llm_error: str | None = PrivateAttr(default=None)


def with_real_tutor(agents: Agents) -> Agents:
    from agents.tutor.agent import run_tutor

    return dataclasses.replace(agents, tutor=run_tutor)


def with_real_memory(agents: Agents) -> Agents:
    from agents.memory.agent import get_profile, update_student_state

    return dataclasses.replace(agents, memory_get=get_profile, memory_update=update_student_state)


def with_real_checker(agents: Agents, llm) -> Agents:
    from agents.checker.agent import analyze as checker_analyze

    def check(req, err):
        stats = {}
        verdict, analysis = checker_analyze(req, llm, stats=stats)
        result = _CheckerVerdict(**verdict.model_dump())
        result._analysis = analysis
        result._llm_error = stats.get("llm_error")
        return result

    def analyze(payload, err):
        verdict = payload["verdict"]
        analysis = getattr(verdict, "_analysis", None)
        llm_error = getattr(verdict, "_llm_error", None)
        if analysis is None:
            if llm_error:
                raise ValueError(f"разбор ошибки недоступен: {llm_error}")
            raise ValueError("проверяющий не вернул разбор ошибки")
        return analysis

    return dataclasses.replace(agents, check=check, analyze=analyze)
