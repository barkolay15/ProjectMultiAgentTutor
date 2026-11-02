"""Банк задач для интерфейса: список задач без тестов и демо-данные для показа.

Задачи берутся из benchmarks/datasets (tasks.json и tasks_sample.json) тем же кодом, что и у проверяющего,
поэтому task_id из интерфейса всегда известен песочнице. Файлы читаются один раз при запуске API:
после правки наборов перезапустите uvicorn.
"""
import json
from functools import lru_cache

from agents.checker.sandbox import DATASETS, load_tasks
from schemas.messages import Task

TOPIC = "mbpp"


@lru_cache(maxsize=1)
def _tasks() -> dict[str, dict]:
    return load_tasks()


@lru_cache(maxsize=1)
def _demo() -> dict[str, dict]:
    path = DATASETS / "checker_set.json"
    if not path.exists():
        return {}
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return {i["task_id"]: i for i in items if "task_id" in i}


def all_tasks() -> list[Task]:
    return [Task(task_id=t["task_id"], statement=t["statement"], topic=TOPIC) for t in _tasks().values()]


def get_task(task_id: str) -> Task | None:
    t = _tasks().get(task_id)
    return Task(task_id=t["task_id"], statement=t["statement"], topic=TOPIC) if t else None


def public_list() -> list[dict]:
    """Список для интерфейса. Тестов здесь нет: студент не должен видеть их все."""
    return [{"task_id": t["task_id"], "entry_point": t.get("entry_point", ""), "statement": t["statement"]}
            for t in _tasks().values()]


def demo_for(task_id: str) -> dict | None:
    """Эталон и неверные решения (мутанты) задачи; только для демонстрации."""
    item = _demo().get(task_id)
    if not item:
        return None
    return {
        "reference": item.get("reference", ""),
        "mutants": [{"bug_type": m.get("bug_type", "other"), "code": m.get("code", "")}
                    for m in item.get("mutants", [])],
    }