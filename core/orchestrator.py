"""Оркестратор (агент B): граф LangGraph, единственная точка обмена между агентами.

Порядок хода:
    load_profile -> pick_task -> [check -> review] -> tutor -> update_memory -> finish

Что делает оркестратор:
  * решает, какой агент нужен дальше (есть код: проверка; нет кода: сразу тьютор);
  * проверяет каждый ответ агента по схеме (parse_reply) и просит повторить;
  * держит лимит шагов, ловит падения агентов, пишет время каждого узла.

Агенты подключаются через dataclass Agents: любой агент это функция
fn(запрос, текст_прошлой_ошибки_или_None) -> объект схемы | JSON-строка.
"""
import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from langgraph.graph import END, StateGraph
from pydantic import BaseModel

from schemas.messages import (
    CheckRequest,
    CheckVerdict,
    ErrorAnalysis,
    MemoryEvent,
    StudentProfile,
    Task,
    TaskRequest,
    TutorReply,
    TutorRequest,
    parse_reply,
)
from schemas.state import SessionState

MAX_STEPS = 12      # защита от зацикливания графа
MAX_ATTEMPTS = 3    # сколько раз просим агента повторить невалидный ответ
DEFAULT_TOPIC = "основы"
FALLBACK_MESSAGE = "Что-то пошло не так на моей стороне. Попробуйте отправить сообщение ещё раз."

AgentFn = Callable[[Any, str | None], Any]


@dataclass
class Agents:
    memory_get: AgentFn                          # (student_id) -> StudentProfile
    memory_update: Callable[[MemoryEvent], None]  # записать событие
    next_task: AgentFn                           # (TaskRequest) -> Task
    check: AgentFn                               # (CheckRequest) -> CheckVerdict
    analyze: AgentFn                             # ({"verdict", "student_code"}) -> ErrorAnalysis
    tutor: AgentFn                               # (TutorRequest) -> TutorReply
    checks_are_real: bool = True                 # результаты заглушки не обучают настоящую память
    memory_reset: Callable[[str], None] | None = None
    memory_backend: str = "stub"


def call_validated(fn: AgentFn, request: Any, model: type[BaseModel],
                   max_attempts: int = MAX_ATTEMPTS) -> tuple[BaseModel, int]:
    """Вызывает агента и проверяет ответ по схеме; при ошибке просит повторить.

    Возвращает (объект, сколько было повторов). Если после max_attempts
    ответ всё ещё невалиден, бросает ValueError.
    """
    error: str | None = None
    for attempt in range(max_attempts):
        raw = fn(request, error)
        if isinstance(raw, model):
            return raw, attempt
        if isinstance(raw, BaseModel):
            error = f"Ожидалась схема {model.__name__}, пришла {type(raw).__name__}"
            continue
        text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, default=str)
        obj, error = parse_reply(model, text)
        if obj is not None:
            return obj, attempt
    raise ValueError(f"{model.__name__}: невалидный ответ после {max_attempts} попыток ({error})")


def _clamp_hint(level: Any) -> int:
    try:
        return max(1, min(3, int(level)))
    except (TypeError, ValueError):
        return 1


def _stop(state: SessionState) -> bool:
    return state.get("status") == "error" or state.get("step_count", 0) >= state.get("max_steps", MAX_STEPS)


def _timed(name: str, fn: Callable[[SessionState], dict]) -> Callable[[SessionState], dict]:
    """Обёртка узла: замер времени, счётчик шагов, перехват падений агента."""
    def wrapper(state: SessionState) -> dict:
        t0 = time.perf_counter()
        try:
            update = dict(fn(state) or {})
        except Exception as e:  # агент упал: фиксируем и уходим в finish
            update = {
                "status": "error",
                "errors": list(state.get("errors", [])) + [f"{name}: {type(e).__name__}: {e}"],
            }
        seconds = round(time.perf_counter() - t0, 4)
        update["step_count"] = state.get("step_count", 0) + 1
        update["timings"] = list(state.get("timings", [])) + [{"node": name, "seconds": seconds}]
        return update
    return wrapper


def build_graph(agents: Agents):
    retries = lambda s, r: s.get("retries", 0) + r  # noqa: E731

    def load_profile(s: SessionState) -> dict:
        try:
            profile, r = call_validated(agents.memory_get, s["student_id"], StudentProfile)
        except Exception as e:
            return {"profile": StudentProfile(student_id=s["student_id"]),
                    "errors": list(s.get("errors", [])) + [f"load_profile: {type(e).__name__}: {e}"]}
        return {"profile": profile, "retries": retries(s, r)}

    def pick_task(s: SessionState) -> dict:
        if s.get("task") is not None:
            return {}
        profile = s["profile"]
        topic = profile.weak_topics[0] if profile.weak_topics else DEFAULT_TOPIC
        req = TaskRequest(student_id=s["student_id"], topic=topic, level=profile.level,
                          solved_task_ids=profile.solved_task_ids)
        task, r = call_validated(agents.next_task, req, Task)
        return {"task": task, "retries": retries(s, r)}

    def check(s: SessionState) -> dict:
        req = CheckRequest(task_id=s["task"].task_id, student_code=s["student_code"])
        verdict, r = call_validated(agents.check, req, CheckVerdict)
        return {"verdict": verdict, "retries": retries(s, r)}

    def review(s: SessionState) -> dict:
        payload = {"verdict": s["verdict"], "student_code": s["student_code"]}
        try:
            analysis, r = call_validated(agents.analyze, payload, ErrorAnalysis)
        except Exception as e:  # без разбора тьютор всё равно может работать по вердикту
            return {"analysis": None, "errors": list(s.get("errors", [])) + [f"review: {e}"]}
        return {"analysis": analysis, "retries": retries(s, r)}

    def tutor(s: SessionState) -> dict:
        # История только текущей задачи; код принадлежит студенту, эталона здесь нет.
        task = s.get("task")
        history = [dict(item) for item in s.get("history", [])
                   if not item.get("task_id") or task is None or item["task_id"] == task.task_id]
        req = TutorRequest(
            student_message=s.get("student_message", ""),
            student_code=s.get("student_code"),
            history=history,
            task=task,
            profile=s.get("profile"),
            verdict=s.get("verdict"),
            analysis=s.get("analysis"),
            hint_level=_clamp_hint(s.get("hint_level", 1)),
        )
        reply, r = call_validated(agents.tutor, req, TutorReply)
        return {"reply": reply, "retries": retries(s, r)}

    def update_memory(s: SessionState) -> dict:
        verdict, task = s.get("verdict"), s.get("task")
        result = "hint_used" if verdict is None else ("pass" if verdict.passed else "fail")
        event = MemoryEvent(
            student_id=s["student_id"],
            topic=task.topic if task else DEFAULT_TOPIC,
            result=result,
            error_type=verdict.error_type if verdict else None,
            task_id=task.task_id if task else None,
            source="real" if agents.checks_are_real else "stub",
        )
        try:
            agents.memory_update(event)
        except Exception as e:
            return {"memory_event": None,
                    "errors": list(s.get("errors", [])) + [f"update_memory: {type(e).__name__}: {e}"]}
        return {"memory_event": event}

    def finish(s: SessionState) -> dict:
        reply, verdict = s.get("reply"), s.get("verdict")
        status = s.get("status")
        if status != "error":
            completed = bool(s.get("timings")) and s["timings"][-1]["node"] == "update_memory"
            status = "ok" if reply is not None and completed else "step_limit"
        hint = _clamp_hint(s.get("hint_level", 1))
        if reply is None:
            reply = TutorReply(message=FALLBACK_MESSAGE, hint_level=hint)
        if verdict is None:
            next_hint = hint
        elif verdict.passed:
            next_hint = 1
        else:
            next_hint = min(3, hint + 1)
        student_entry = {"role": "student", "text": s.get("student_message", "")}
        tutor_entry = {"role": "tutor", "text": reply.message}
        if s.get("student_code"):
            student_entry["code"] = s["student_code"]
        if s.get("task"):
            student_entry["task_id"] = tutor_entry["task_id"] = s["task"].task_id
        history = list(s.get("history", [])) + [student_entry, tutor_entry]
        return {
            "reply": reply,
            "status": status,
            "history": history,
            "next_hint_level": next_hint,
            # Решённая задача остаётся открытой для обсуждения и повторной проверки.
            "next_task": s.get("task"),
        }

    def after(next_node: str):
        return lambda s: "finish" if _stop(s) else next_node

    def after_tutor(s: SessionState) -> str:
        if s.get("step_count", 0) >= s.get("max_steps", MAX_STEPS):
            return "finish"
        if s.get("status") == "error" and s.get("verdict") is None:
            return "finish"
        return "update_memory"

    g = StateGraph(SessionState)
    for name, fn in [("load_profile", load_profile), ("pick_task", pick_task), ("check", check),
                     ("review", review), ("tutor", tutor), ("update_memory", update_memory),
                     ("finish", finish)]:
        g.add_node(name, _timed(name, fn))

    g.set_entry_point("load_profile")
    g.add_conditional_edges("load_profile", after("pick_task"), {"pick_task": "pick_task", "finish": "finish"})
    g.add_conditional_edges(
        "pick_task",
        lambda s: "finish" if _stop(s) else ("check" if s.get("student_code") else "tutor"),
        {"check": "check", "tutor": "tutor", "finish": "finish"},
    )
    # Условное ребро: провал проверки уходит на узел ревью (разбор ошибки), успех сразу к тьютору.
    g.add_conditional_edges(
        "check",
        lambda s: "finish" if _stop(s) else ("tutor" if s["verdict"].passed else "review"),
        {"review": "review", "tutor": "tutor", "finish": "finish"},
    )
    g.add_conditional_edges("review", after("tutor"), {"tutor": "tutor", "finish": "finish"})
    # Сбой тьютора не должен терять уже полученный вердикт для памяти.
    g.add_conditional_edges("tutor", after_tutor, {"update_memory": "update_memory", "finish": "finish"})
    g.add_edge("update_memory", "finish")
    g.add_edge("finish", END)
    return g.compile()


def _initial_state(student_id, message, code, task, hint_level, history, max_steps) -> SessionState:
    return {
        "student_id": student_id,
        "student_message": message,
        "student_code": code or None,
        "task": task,
        "hint_level": _clamp_hint(hint_level),
        "history": list(history or []),
        "step_count": 0,
        "max_steps": max_steps,
        "retries": 0,
        "errors": [],
        "timings": [],
        "status": "ok",
    }


def _default_agents() -> Agents:
    from core.stubs import stub_agents  # импорт здесь, чтобы не было цикла
    return stub_agents()


def run_turn(student_id: str, message: str, code: str | None = None, task: Task | None = None,
             hint_level: int = 1, history: list[dict] | None = None,
             agents: Agents | None = None, max_steps: int = MAX_STEPS) -> SessionState:
    """Один ход диалога: от сообщения студента до ответа тьютора."""
    state = _initial_state(student_id, message, code, task, hint_level, history, max_steps)
    t0 = time.perf_counter()
    out = build_graph(agents or _default_agents()).invoke(state)
    out["total_seconds"] = round(time.perf_counter() - t0, 4)
    return out


def stream_turn(student_id: str, message: str, code: str | None = None, task: Task | None = None,
                hint_level: int = 1, history: list[dict] | None = None,
                agents: Agents | None = None, max_steps: int = MAX_STEPS):
    """Тот же ход, но по шагам: после каждого узла графа отдаёт {"type": "step", "node", "seconds"},
    в конце {"type": "final", "state": итоговое состояние}. Интерфейс показывает по ним прогресс."""
    state = _initial_state(student_id, message, code, task, hint_level, history, max_steps)
    t0 = time.perf_counter()
    last, seen = state, 0
    for snapshot in build_graph(agents or _default_agents()).stream(state, stream_mode="values"):
        last = snapshot
        timings = snapshot.get("timings", [])
        while seen < len(timings):
            yield {"type": "step", **timings[seen]}
            seen += 1
    last["total_seconds"] = round(time.perf_counter() - t0, 4)
    yield {"type": "final", "state": last}
