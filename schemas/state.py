"""Общее состояние сессии: живёт в графе оркестратора (LangGraph).

Агенты состояние напрямую не видят: оркестратор сам берёт из него нужное
и собирает JSON-запросы по схемам из messages.py.
"""
from typing import Any, TypedDict

from schemas.messages import (
    CheckVerdict,
    ErrorAnalysis,
    MemoryEvent,
    StudentProfile,
    Task,
    TutorReply,
)


class SessionState(TypedDict, total=False):
    # --- вход одного хода ---
    student_id: str
    student_message: str
    student_code: str | None      # код студента, если он его прислал
    task: Task | None             # текущая задача (None: нужно выбрать новую)
    hint_level: int               # 1..3, уровень подсказки на этом ходу
    history: list[dict[str, Any]]

    # --- что накопили агенты ---
    profile: StudentProfile | None
    verdict: CheckVerdict | None
    analysis: ErrorAnalysis | None
    reply: TutorReply | None
    memory_event: MemoryEvent | None

    # --- служебное ---
    step_count: int
    max_steps: int
    retries: int                  # сколько раз просили агента повторить ответ
    errors: list[str]
    timings: list[dict[str, Any]]  # [{"node": ..., "seconds": ...}]
    total_seconds: float
    status: str                   # "ok" | "error" | "step_limit"

    # --- что сохранить до следующего хода ---
    next_task: Task | None
    next_hint_level: int