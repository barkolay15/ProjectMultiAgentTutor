"""Выбор нерешённой задачи по профилю, без LLM и без доступа к сессиям API."""
from collections.abc import Iterable

from schemas.messages import Task, TaskRequest

LEVELS = {"beginner": 0, "intermediate": 1, "advanced": 2}


def select_task(tasks: list[Task], request: TaskRequest, solved: Iterable[str] = ()) -> Task:
    if not tasks:
        raise ValueError("Банк задач пуст")
    done = set(request.solved_task_ids) | set(solved)
    candidates = [task for task in tasks if task.task_id not in done] or tasks
    target = LEVELS[request.level]

    def rank(task: Task):
        level = LEVELS.get(task.level)
        return (task.topic != request.topic,
                abs(level - target) if level is not None else len(LEVELS),
                level > target if level is not None else False)

    # При одинаковой пригодности сохраняется порядок банка. Если всё решено — повторение.
    return min(candidates, key=rank)
