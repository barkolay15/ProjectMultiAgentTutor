"""Подбор задач по доступной разметке и сохранённому прогрессу."""
import pytest

from api import bank
from core.task_selection import select_task
from schemas.messages import Task, TaskRequest


def task(tid, topic="mbpp", level=None):
    return Task(task_id=tid, statement="Условие", topic=topic, level=level)


def test_profile_topic_and_level_determine_selection():
    tasks = [task("strings", "строки", "intermediate"), task("easy", "циклы", "beginner"),
             task("match", "циклы", "intermediate"), task("hard", "циклы", "advanced")]
    request = TaskRequest(student_id="s", topic="циклы", level="intermediate")
    assert select_task(tasks, request).task_id == "match"
    request.solved_task_ids = ["match"]
    assert select_task(tasks, request).task_id == "easy"
    assert select_task(tasks, request, solved=["easy"]).task_id == "hard"


def test_unannotated_bank_keeps_order_and_excludes_completed_tasks():
    tasks = [task("one"), task("two")]
    request = TaskRequest(student_id="s", topic="строки", solved_task_ids=["one"])
    assert select_task(tasks, request).task_id == "two"
    assert select_task(tasks, request, solved=["two"]).task_id == "one"  # Повторение, если всё решено.
    with pytest.raises(ValueError, match="Банк задач пуст"):
        select_task([], request)


def test_bank_preserves_metadata_and_supports_old_records(monkeypatch):
    monkeypatch.setattr(bank, "_tasks", lambda: {
        "one": {"task_id": "one", "statement": "Условие", "topic": "строки", "difficulty": "medium"},
        "two": {"task_id": "two", "statement": "Старое условие"},
    })
    assert bank.get_task("one").topic == "строки"
    assert bank.get_task("one").level == "intermediate"
    assert bank.get_task("two").topic == "mbpp" and bank.get_task("two").level is None
