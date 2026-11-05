"""HTTP-ручки: интерфейс отправляет сюда сообщения, оркестратор отвечает."""
import dataclasses
import json
import logging
import os
import threading
from collections import deque
from pathlib import Path as FilePath

from dotenv import load_dotenv

load_dotenv(FilePath(__file__).resolve().parents[1] / ".env")

from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from api import bank
from agents.checker.prompts import PROMPT_VERSION as CHECKER_PROMPT_VERSION
from core.llm_client import MODEL_NAME
from core.metrics import summarize
from core.orchestrator import MAX_STEPS, Agents, run_turn, stream_turn
from core.stubs import stub_agents

log = logging.getLogger("tutor.api")
if not log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)

router = APIRouter()

# По умолчанию заглушки. USE_REAL_CHECKER=1 подключает настоящего проверяющего (нужен ключ в .env).
_agents: Agents = stub_agents()
_real_tutor = bool(os.getenv("GIGACHAT_CREDENTIALS"))
if _real_tutor:
    from core.adapters import with_real_tutor
    _agents = with_real_tutor(_agents)
_real_checker = os.getenv("USE_REAL_CHECKER") == "1"
if _real_checker:
    from core.adapters import with_real_checker
    from core.llm_client import get_llm
    _agents = with_real_checker(_agents, get_llm())

_sessions: dict[str, dict] = {}              # student_id -> {"task", "hint_level", "history", "solved"}
_runs: deque[dict] = deque(maxlen=1000)      # последние ходы для /metrics
_state_lock = threading.Lock()               # защищает _sessions, _runs и _student_locks
_student_locks: dict[str, threading.Lock] = {}


def _new_session() -> dict:
    return {"task": None, "hint_level": 1, "history": [], "solved": []}


def _bank_next_task(req, err):
    """Следующая нерешённая задача из банка (настоящий проверяющий знает только задачи банка)."""
    tasks = bank.all_tasks()
    with _state_lock:
        solved = set(_sessions.get(req.student_id, {}).get("solved", []))
    return next((t for t in tasks if t.task_id not in solved), tasks[0])


if _real_checker and bank.all_tasks():
    _agents = dataclasses.replace(_agents, next_task=_bank_next_task)


def set_agents(agents: Agents) -> None:
    global _agents
    _agents = agents


def _student_lock(student_id: str) -> threading.Lock:
    """Ходы одного студента выполняются по очереди, ходы разных студентов параллельно."""
    with _state_lock:
        return _student_locks.setdefault(student_id, threading.Lock())


class ChatRequest(BaseModel):
    student_id: str = Field(min_length=1, max_length=64, pattern=r"^[\w.\-]+$")
    message: str = Field(min_length=1, max_length=2000)
    code: str | None = Field(default=None, max_length=10000)
    task_id: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("пустое сообщение")
        return v

    @field_validator("code")
    @classmethod
    def _blank_code_is_none(cls, v: str | None) -> str | None:
        return v if v and v.strip() else None


def _dump(obj):
    return obj.model_dump() if obj is not None else None


def _demo_enabled() -> bool:
    return os.getenv("TUTOR_DEMO") == "1"


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/info")
def info():
    """Режим работы: интерфейс показывает, какие агенты настоящие, а какие заглушки."""
    return {
        "checker": "real" if _real_checker else "stub",
        "tutor": "llm" if _real_tutor else "stub",
        "max_steps": MAX_STEPS,
        "bank": len(bank.all_tasks()),
        "demo": _demo_enabled(),
        "model": MODEL_NAME if _real_checker or _real_tutor else None,
        "checker_prompt_version": CHECKER_PROMPT_VERSION,
    }


def _commit_turn(req: ChatRequest, sess: dict, out: dict) -> dict:
    """Сохраняет итог хода в сессии студента и собирает ответ API."""
    with _state_lock:
        sess.update(
            task=out.get("next_task"),
            hint_level=out.get("next_hint_level", 1),
            history=out["history"][-50:],
        )
        verdict, solved_task = out.get("verdict"), out.get("task")
        if verdict is not None and verdict.passed and solved_task is not None:
            if solved_task.task_id not in sess["solved"]:
                sess["solved"].append(solved_task.task_id)
        solved = list(sess["solved"])
        _runs.append({"seconds": out["total_seconds"], "status": out["status"]})
    log.info("turn: student=%s status=%s seconds=%s retries=%s errors=%d",
             req.student_id, out["status"], out["total_seconds"], out.get("retries", 0),
             len(out.get("errors", [])))
    return {
        "reply": out["reply"].message,
        "hint_level": out["reply"].hint_level,
        "task": _dump(out.get("task")),
        "verdict": _dump(out.get("verdict")),
        "analysis": _dump(out.get("analysis")),
        "status": out["status"],
        "retries": out.get("retries", 0),
        "errors": out.get("errors", []),
        "timings": out.get("timings", []),
        "total_seconds": out["total_seconds"],
        "solved": solved,
    }


def _session_for_turn(req: ChatRequest):
    with _state_lock:
        sess = _sessions.setdefault(req.student_id, _new_session())
        if req.task_id is not None and (sess["task"] is None or sess["task"].task_id != req.task_id):
            raise HTTPException(status_code=409, detail="Текущая задача изменилась. Выберите задачу заново.")
        return sess, sess["task"], sess["hint_level"], list(sess["history"])


@router.post("/chat")
def chat(req: ChatRequest):
    with _student_lock(req.student_id):
        sess, task, hint_level, history = _session_for_turn(req)
        try:
            out = run_turn(
                student_id=req.student_id,
                message=req.message,
                code=req.code,
                task=task,
                hint_level=hint_level,
                history=history,
                agents=_agents,
            )
        except Exception:
            log.exception("turn failed: student=%s", req.student_id)
            raise HTTPException(status_code=500, detail="Оркестратор не смог обработать ход.")
        return _commit_turn(req, sess, out)


def _turn_events(req: ChatRequest):
    """События одного хода для потоковой ручки: step (после каждого узла графа), затем result или error."""
    with _student_lock(req.student_id):
        final = None
        try:
            sess, task, hint_level, history = _session_for_turn(req)
            for ev in stream_turn(
                student_id=req.student_id, message=req.message, code=req.code,
                task=task, hint_level=hint_level, history=history, agents=_agents,
            ):
                if ev["type"] == "step":
                    yield ev
                else:
                    final = ev["state"]
            payload = _commit_turn(req, sess, final)
        except HTTPException as e:
            yield {"type": "error", "message": e.detail, "status": e.status_code}
            return
        except Exception:
            log.exception("stream turn failed: student=%s", req.student_id)
            yield {"type": "error", "message": "Оркестратор не смог обработать ход."}
            return
    yield {"type": "result", "data": payload}


@router.post("/chat/stream")
def chat_stream(req: ChatRequest):
    """Тот же ход, что и /chat, но построчным JSON (NDJSON): интерфейс показывает шаги по мере выполнения."""
    def lines():
        for ev in _turn_events(req):
            yield json.dumps(ev, ensure_ascii=False) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/session/{student_id}")
def session(student_id: str = Path(min_length=1, max_length=64)):
    """Состояние сессии: после обновления страницы интерфейс подтягивает отсюда диалог."""
    with _state_lock:
        sess = _sessions.get(student_id)
        if sess is None:
            return {"student_id": student_id, "hint_level": 1, "task": None, "history": [], "solved": []}
        return {
            "student_id": student_id,
            "hint_level": sess["hint_level"],
            "task": _dump(sess["task"]),
            "history": list(sess["history"]),
            "solved": list(sess["solved"]),
        }


@router.get("/tasks")
def tasks():
    """Банк задач для выбора в интерфейсе (без тестов)."""
    return bank.public_list()


class TaskChoice(BaseModel):
    task_id: str = Field(min_length=1, max_length=100)


@router.post("/session/{student_id}/task")
def choose_task(choice: TaskChoice, student_id: str = Path(min_length=1, max_length=64)):
    """Студент выбрал задачу из банка: подсказки начинаются заново с первого уровня."""
    task = bank.get_task(choice.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Такой задачи нет в банке.")
    with _student_lock(student_id):
        with _state_lock:
            sess = _sessions.setdefault(student_id, _new_session())
            sess.update(task=task, hint_level=1)
    return {"task": task.model_dump(), "hint_level": 1, "starter": bank.starter(task.task_id)}


@router.get("/demo/{task_id:path}")
def demo(task_id: str):
    """Эталон и неверные решения для показа. Выключено по умолчанию: включается TUTOR_DEMO=1."""
    if not _demo_enabled():
        raise HTTPException(status_code=404, detail="Демо-режим выключен.")
    data = bank.demo_for(task_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Для этой задачи нет демо-данных.")
    return data


@router.post("/reset/{student_id}")
def reset(student_id: str = Path(min_length=1, max_length=64)):
    with _student_lock(student_id):
        with _state_lock:
            _sessions.pop(student_id, None)
    return {"reset": student_id}


@router.get("/metrics")
def metrics():
    with _state_lock:
        runs = list(_runs)
    return summarize(runs)
