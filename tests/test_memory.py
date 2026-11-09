"""Тесты агента памяти: без ключей и без сети."""
import os
import sqlite3

from agents.memory.agent import get_profile, reset_memory, update_student_state
from schemas.messages import MemoryEvent


def ev(result, topic="циклы", error_type=None, sid="s1"):
    return MemoryEvent(student_id=sid, topic=topic, result=result, error_type=error_type)


def test_new_student_gets_empty_beginner_profile():
    p = get_profile("new")
    assert p.level == "beginner"
    assert p.weak_topics == [] and p.typical_errors == []


def test_fails_make_topic_weak_and_record_error_types():
    update_student_state(ev("fail", error_type="AssertionError"))
    update_student_state(ev("fail", error_type="AssertionError"))
    update_student_state(ev("fail", topic="строки", error_type="SyntaxError"))
    p = get_profile("s1")
    assert p.weak_topics == ["циклы", "строки"]
    assert p.typical_errors == ["AssertionError", "SyntaxError"]


def test_passes_cancel_fails():
    update_student_state(ev("fail"))
    update_student_state(ev("pass"))
    assert get_profile("s1").weak_topics == []


def test_hint_used_is_not_an_error():
    for _ in range(5):
        update_student_state(ev("hint_used"))
    p = get_profile("s1")
    assert p.weak_topics == [] and p.typical_errors == []


def test_data_is_stored_in_sqlite_file_and_survives_new_connection():
    update_student_state(ev("fail", error_type="AssertionError"))
    path = os.environ["MEMORY_DB"]
    assert os.path.exists(path)
    rows = sqlite3.connect(path).execute("SELECT kind, key, count FROM stats ORDER BY kind").fetchall()
    assert rows == [("error", "AssertionError", 1), ("fail", "циклы", 1)]
    assert get_profile("s1").weak_topics == ["циклы"]  # читаем уже заново из файла


def test_reset_memory_clears_everything():
    update_student_state(ev("fail"))
    reset_memory()
    assert get_profile("s1").weak_topics == []


def test_students_are_independent():
    update_student_state(ev("fail", sid="a"))
    assert get_profile("b").weak_topics == []


def test_level_grows_with_passes():
    for _ in range(5):
        update_student_state(ev("pass"))
    assert get_profile("s1").level == "intermediate"
    for _ in range(10):
        update_student_state(ev("pass"))
    assert get_profile("s1").level == "advanced"


def test_works_inside_orchestrator():
    import dataclasses

    from agents.memory.agent import get_profile as gp, update_student_state as us
    from core.orchestrator import run_turn
    from core.stubs import stub_agents

    agents = dataclasses.replace(stub_agents(), memory_get=gp, memory_update=us, checks_are_real=True)
    run_turn("s9", "проверьте", code="def strlen(s): return 0", agents=agents)
    assert get_profile("s9").weak_topics  # провал записался в память


def test_repeated_success_for_one_task_does_not_raise_level():
    event = ev("pass").model_copy(update={"task_id": "task/one"})
    for _ in range(20):
        update_student_state(event)
    profile = get_profile("s1")
    assert profile.level == "beginner"
    assert profile.solved_task_ids == ["task/one"]
    with sqlite3.connect(os.environ["MEMORY_DB"]) as conn:
        assert conn.execute("SELECT count FROM stats WHERE kind = 'pass'").fetchone()[0] == 1


def test_distinct_tasks_raise_level_and_persist_progress_per_student():
    for i in range(5):
        update_student_state(ev("pass").model_copy(update={"task_id": f"task/{i}"}))
    assert get_profile("s1").level == "intermediate"
    assert get_profile("s1").solved_task_ids == [f"task/{i}" for i in range(5)]
    assert get_profile("other").solved_task_ids == []
    update_student_state(ev("pass", sid="other").model_copy(update={"task_id": "task/0"}))
    assert get_profile("other").solved_task_ids == ["task/0"]


def test_concurrent_successes_for_same_task_are_counted_once():
    from concurrent.futures import ThreadPoolExecutor

    get_profile("parallel")  # Схема создана до параллельных записей.
    event = ev("pass", sid="parallel").model_copy(update={"task_id": "task/one"})
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(update_student_state, [event] * 8))
    with sqlite3.connect(os.environ["MEMORY_DB"]) as conn:
        assert conn.execute("SELECT count FROM stats WHERE student_id = 'parallel' AND kind = 'pass'").fetchone()[0] == 1
    assert get_profile("parallel").solved_task_ids == ["task/one"]


def test_stub_events_do_not_touch_database(monkeypatch):
    from unittest.mock import Mock
    from agents.memory import agent

    connect = Mock(side_effect=AssertionError("Демо не должно записываться в базу"))
    monkeypatch.setattr(agent, "_connect", connect)
    for result in ("pass", "fail", "hint_used"):
        update_student_state(ev(result).model_copy(update={"source": "stub", "task_id": "stub/strlen"}))
    connect.assert_not_called()


def test_failed_counter_write_rolls_back_task_completion(monkeypatch):
    import pytest
    from agents.memory import agent

    with monkeypatch.context() as patch:
        patch.setattr(agent, "_UPSERT", "INSERT INTO missing_table VALUES (?, ?, ?)")
        with pytest.raises(sqlite3.OperationalError):
            update_student_state(ev("pass").model_copy(update={"task_id": "task/one"}))
    assert get_profile("s1").solved_task_ids == []
    update_student_state(ev("pass").model_copy(update={"task_id": "task/one"}))
    assert get_profile("s1").solved_task_ids == ["task/one"]


def test_existing_database_keeps_stats_when_progress_table_is_added():
    with sqlite3.connect(os.environ["MEMORY_DB"]) as conn:
        conn.execute("CREATE TABLE stats (student_id TEXT, kind TEXT, key TEXT, count INTEGER, "
                     "PRIMARY KEY (student_id, kind, key))")
        conn.execute("INSERT INTO stats VALUES ('s1', 'fail', 'строки', 2)")
    profile = get_profile("s1")
    assert profile.weak_topics == ["строки"] and profile.solved_task_ids == []
    update_student_state(ev("pass", topic="строки").model_copy(update={"task_id": "task/new"}))
    assert get_profile("s1").weak_topics == ["строки"]
    assert get_profile("s1").solved_task_ids == ["task/new"]


def test_reset_clears_completed_tasks_as_well_as_stats():
    update_student_state(ev("pass").model_copy(update={"task_id": "task/one"}))
    reset_memory()
    assert get_profile("s1").solved_task_ids == []
    update_student_state(ev("pass").model_copy(update={"task_id": "task/one"}))
    assert get_profile("s1").solved_task_ids == ["task/one"]
