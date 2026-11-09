"""Заглушки агентов: граф работает целиком без ключей и без сети.

Настоящие агенты подключаются адаптерами из core/adapters.py.
Тесты и демо остаются на заглушках; их результаты не обучают настоящую память.
"""
from core.orchestrator import Agents
from schemas.messages import (
    CheckVerdict,
    ErrorAnalysis,
    StudentProfile,
    Task,
    TutorReply,
)


def stub_agents() -> Agents:
    def memory_get(student_id, err):          # C: агент памяти
        return StudentProfile(student_id=student_id)

    def memory_update(event):                 # C: update_student_state
        return None

    def memory_reset(student_id):
        return None

    def next_task(req, err):                  # E: постановщик
        return Task(
            task_id="stub/strlen",
            statement="Напишите функцию strlen(s), которая возвращает длину строки.",
            topic=req.topic,
            example={"input": "''", "output": "0"},
        )

    def check(req, err):                      # D: run_tests -> вердикт
        ok = "return len(" in req.student_code
        verdict = CheckVerdict(
            task_id=req.task_id,
            passed=ok,
            failed_tests=[] if ok else ["assert strlen('') == 0"],
            error_type=None if ok else "AssertionError",
        )
        return verdict.model_dump_json()      # строка JSON: проверка схемы идёт через parse_reply

    def analyze(payload, err):                # D: разбор ошибки словами
        verdict = payload["verdict"]
        return ErrorAnalysis(
            task_id=verdict.task_id,
            category="logic",
            summary="Функция возвращает не тот результат, что ожидают тесты.",
            hint_for_tutor="Спросите, что должна вернуть функция для пустой строки.",
        ).model_dump_json()

    def tutor(req, err):                      # A: тьютор
        if req.verdict is not None and req.verdict.passed:
            text = "Все тесты прошли. Объясните своими словами, почему это работает."
        elif req.verdict is not None:
            text = "Один тест не прошёл. Что должна вернуть функция для пустой строки?"
        else:
            text = "С чего, по-вашему, стоит начать решение этой задачи?"
        return TutorReply(message=text, hint_level=req.hint_level).model_dump_json()

    return Agents(memory_get, memory_update, next_task, check, analyze, tutor,
                  checks_are_real=False, memory_reset=memory_reset)
