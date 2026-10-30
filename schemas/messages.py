"""JSON-контракты между агентами репетитора.

Правило: LLM предлагает JSON, код проверяет его по этим схемам.
Каждое сообщение между агентами проходит через parse_reply(): сначала
синтаксис (json.loads), потом поля и типы (pydantic).
"""
import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError


# ---------- Проверяющий (D) ----------

class CheckRequest(BaseModel):
    """Оркестратор -> Проверяющий."""
    task_id: str = Field(description="Например, 'humaneval/23'")
    student_code: str = Field(description="Код студента одной строкой, с экранированием")


class FailedTest(BaseModel):
    test: str = Field(description="Строка проваленного теста, например 'assert f(1) == 2'")
    got: str | None = Field(default=None, description="Фактический результат кода (repr), если удалось получить")


class CheckVerdict(BaseModel):
    """Проверяющий -> Оркестратор. Вердикт даёт запуск тестов, а не мнение модели."""
    task_id: str
    passed: bool
    failed_tests: list[str] = Field(default_factory=list)
    failed_details: list[FailedTest] = Field(default_factory=list)
    error_type: str | None = Field(
        default=None, description="Например, 'AssertionError', 'SyntaxError', 'Timeout'"
    )
    traceback_short: str | None = None


class ErrorAnalysis(BaseModel):
    """Проверяющий -> Оркестратор -> Тьютор: разбор ошибки словами, без решения.

    Пишет LLM, но код проверяет, что в тексте нет программы (см. agent.py).
    """
    task_id: str
    category: Literal["syntax", "logic", "edge_case", "runtime", "timeout", "other"]
    summary: str = Field(description="1-2 предложения: что идёт не так, без кода")
    hint_for_tutor: str = Field(description="Наводящая мысль для тьютора, без готового решения")


# ---------- Память (C) ----------

class MemoryEvent(BaseModel):
    """Оркестратор -> Память: что произошло на шаге."""
    student_id: str
    topic: str = Field(description="Тема, например 'циклы'")
    result: Literal["pass", "fail", "hint_used"]
    error_type: str | None = None


class StudentProfile(BaseModel):
    """Память -> Оркестратор: выжимка профиля для тьютора."""
    student_id: str
    level: Literal["beginner", "intermediate", "advanced"] = "beginner"
    weak_topics: list[str] = Field(default_factory=list)
    typical_errors: list[str] = Field(default_factory=list)


# ---------- Постановщик (E) ----------

class TaskRequest(BaseModel):
    """Оркестратор -> Постановщик."""
    student_id: str
    topic: str
    level: Literal["beginner", "intermediate", "advanced"] = "beginner"


class Task(BaseModel):
    """Постановщик -> Оркестратор. Тесты хранятся у проверяющего, сюда не попадают."""
    task_id: str
    statement: str
    topic: str


# ---------- Тьютор (A) ----------

class TutorRequest(BaseModel):
    """Оркестратор -> Тьютор. Готового решения здесь нет."""
    student_message: str
    task: Task | None = None
    profile: StudentProfile | None = None
    verdict: CheckVerdict | None = None
    hint_level: Literal[1, 2, 3] = 1


class TutorReply(BaseModel):
    """Тьютор -> Оркестратор."""
    message: str = Field(description="Ответ студенту: вопрос или подсказка")
    hint_level: Literal[1, 2, 3]
    asks_to_run_code: bool = False


# ---------- Проверка JSON от LLM ----------

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_reply(model: type[BaseModel], text: str) -> tuple[BaseModel | None, str | None]:
    """Возвращает (объект, None) или (None, текст_ошибки).

    Ошибку оркестратор вставляет в повторный запрос агенту.
    Два уровня: синтаксис JSON, затем схема.
    """
    cleaned = _FENCE.sub("", text.strip())
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        return None, f"Невалидный JSON: {e}"
    try:
        return model.model_validate(data), None
    except ValidationError as e:
        return None, f"Не подходит под схему {model.__name__}: {e.errors()[0]['msg']} ({e.errors()[0]['loc']})"
