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

    agents = dataclasses.replace(stub_agents(), memory_get=gp, memory_update=us)
    run_turn("s9", "проверьте", code="def strlen(s): return 0", agents=agents)
    assert get_profile("s9").weak_topics  # провал записался в память
