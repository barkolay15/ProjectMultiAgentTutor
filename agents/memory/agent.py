"""Агент памяти (C): помнит, на каких темах и на каких ошибках студент спотыкается.

Без LLM и без ключей: вся логика это подсчёт событий. Счётчики лежат в SQLite
(стандартная библиотека), поэтому профили переживают перезапуск API.

Файл базы: переменная окружения MEMORY_DB, по умолчанию data/memory.db в корне проекта.

Оркестратор вызывает две функции:
    get_profile(student_id, err)  -> StudentProfile   (в начале хода, узел load_profile)
    update_student_state(event)   -> None             (в конце хода, узел update_memory)
"""
import os
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

from schemas.messages import MemoryEvent, StudentProfile

MAX_WEAK_TOPICS = 3
MAX_TYPICAL_ERRORS = 3

# Порог уровня: сколько успешных решений и какая доля успехов от всех проверок.
INTERMEDIATE_PASSES, INTERMEDIATE_RATIO = 5, 0.6
ADVANCED_PASSES, ADVANCED_RATIO = 15, 0.8

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "memory.db"

# Одна таблица-счётчик: (студент, вид, ключ) -> сколько раз.
# kind: "pass" и "fail" и "hint" (key = тема), "error" (key = тип ошибки).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS stats (
    student_id TEXT NOT NULL,
    kind       TEXT NOT NULL,
    key        TEXT NOT NULL,
    count      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (student_id, kind, key)
)
"""
_SOLVED_SCHEMA = """
CREATE TABLE IF NOT EXISTS solved_tasks (
    student_id TEXT NOT NULL,
    task_id    TEXT NOT NULL,
    PRIMARY KEY (student_id, task_id)
)
"""
_UPSERT = """
INSERT INTO stats (student_id, kind, key, count) VALUES (?, ?, ?, 1)
ON CONFLICT (student_id, kind, key) DO UPDATE SET count = count + 1
"""


def _db_path() -> str:
    return os.getenv("MEMORY_DB") or str(DEFAULT_DB)


def _connect() -> sqlite3.Connection:
    """Новое соединение на каждый вызов: так безопасно при нескольких потоках API."""
    path = _db_path()
    Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_SCHEMA)
    conn.execute(_SOLVED_SCHEMA)
    return conn


def update_student_state(event: MemoryEvent) -> None:
    """Записать, что произошло на шаге."""
    if event.source == "stub":
        return
    rows = []
    if event.result == "pass":
        rows.append(("pass", event.topic))
    elif event.result == "fail":
        rows.append(("fail", event.topic))
        if event.error_type:
            rows.append(("error", event.error_type))
    else:  # "hint_used": студент написал без кода, это не ошибка
        rows.append(("hint", event.topic))
    with closing(_connect()) as conn, conn:  # conn как контекст = транзакция
        if event.result == "pass" and event.task_id:
            inserted = conn.execute(
                "INSERT OR IGNORE INTO solved_tasks (student_id, task_id) VALUES (?, ?)",
                (event.student_id, event.task_id),
            )
            if inserted.rowcount == 0:
                return  # Повторная проверка не повышает уровень; всё в одной транзакции.
        conn.executemany(_UPSERT, [(event.student_id, k, v) for k, v in rows])


def _level(passes: int, fails: int) -> str:
    total = passes + fails
    ratio = passes / total if total else 0.0
    if passes >= ADVANCED_PASSES and ratio >= ADVANCED_RATIO:
        return "advanced"
    if passes >= INTERMEDIATE_PASSES and ratio >= INTERMEDIATE_RATIO:
        return "intermediate"
    return "beginner"


def get_profile(student_id: str, err: str | None = None) -> StudentProfile:
    """Выжимка профиля для тьютора и постановщика.

    weak_topics: темы, где ошибок больше, чем успехов; самая слабая первая
    (оркестратор берёт weak_topics[0] как тему следующей задачи).
    """
    counters: dict[str, Counter] = {"pass": Counter(), "fail": Counter(), "error": Counter()}
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT kind, key, count FROM stats WHERE student_id = ?", (student_id,)
        ).fetchall()
        solved = [row[0] for row in conn.execute(
            "SELECT task_id FROM solved_tasks WHERE student_id = ? ORDER BY task_id", (student_id,)
        )]
    for kind, key, count in rows:
        if kind in counters:
            counters[kind][key] = count
    passes, fails, errors = counters["pass"], counters["fail"], counters["error"]

    gap = {t: fails[t] - passes[t] for t in fails}
    weak = sorted((t for t, g in gap.items() if g > 0), key=lambda t: (-gap[t], t))
    return StudentProfile(
        student_id=student_id,
        level=_level(sum(passes.values()), sum(fails.values())),
        weak_topics=weak[:MAX_WEAK_TOPICS],
        typical_errors=[e for e, _ in errors.most_common(MAX_TYPICAL_ERRORS)],
        solved_task_ids=solved,
    )


def reset_memory() -> None:
    """Очистить всю базу (для тестов)."""
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM stats")
        conn.execute("DELETE FROM solved_tasks")
