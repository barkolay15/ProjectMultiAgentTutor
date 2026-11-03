"""Банк задач для интерфейса: список задач без тестов, заготовки кода и демо-данные для показа.

Задачи берутся из benchmarks/datasets (tasks.json и tasks_sample.json) тем же кодом, что и у проверяющего,
поэтому task_id из интерфейса всегда известен песочнице. Файлы читаются один раз при запуске API:
после правки наборов перезапустите uvicorn.
"""
import json
from functools import lru_cache

from agents.checker.sandbox import DATASETS, load_tasks
from api.starter import make_starter
from core.examples import example_from_test
from schemas.messages import Task

TOPIC = "mbpp"
HIDDEN_PREFIXES = ("stub/",)     # служебные задачи заглушек студенту не показываем


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


@lru_cache(maxsize=1)
def _starters() -> dict[str, str]:
    """Заготовки def-строк. Эталонное решение читается только ради сигнатуры, тело наружу не уходит."""
    demo = _demo()
    return {tid: make_starter(t, (demo.get(tid) or {}).get("reference")) for tid, t in _tasks().items()}


def _visible(task_id: str) -> bool:
    return not task_id.startswith(HIDDEN_PREFIXES)


def _as_task(t: dict) -> Task:
    return Task(task_id=t["task_id"], statement=t["statement"], topic=TOPIC, example=_example(t))


def _example(t: dict) -> dict | None:
    return next((example for test in t.get("tests", []) if (example := example_from_test(test))), None)


def all_tasks() -> list[Task]:
    return [_as_task(t) for tid, t in _tasks().items() if _visible(tid)]


def get_task(task_id: str) -> Task | None:
    t = _tasks().get(task_id)
    return _as_task(t) if t else None


def starter(task_id: str) -> str:
    return _starters().get(task_id, "")


def public_list() -> list[dict]:
    """Список для интерфейса. Тестов и эталонов здесь нет: студент не должен видеть их все."""
    out = []
    for tid, t in _tasks().items():
        if not _visible(tid):
            continue
        item = {"task_id": tid, "entry_point": t.get("entry_point", ""), "statement": t["statement"],
                "starter": _starters().get(tid, ""), "example": _example(t)}
        if t.get("difficulty"):
            item["difficulty"] = t["difficulty"]
        out.append(item)
    return out


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
