"""HTTP-ручки: интерфейс отправляет сюда сообщения, оркестратор отвечает."""
import logging
import os
import threading
from collections import deque

from dotenv import load_dotenv

load_dotenv()

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field, field_validator

from core.metrics import summarize
from core.orchestrator import MAX_STEPS, Agents, run_turn
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
_real_checker = os.getenv("USE_REAL_CHECKER") == "1"
if _real_checker:
    from core.adapters import with_real_checker
    from core.llm_client import get_llm
    _agents = with_real_checker(_agents, get_llm())

_sessions: dict[str, dict] = {}              # student_id -> {"task", "hint_level", "history"}
_runs: deque[dict] = deque(maxlen=1000)      # последние ходы для /metrics
_state_lock = threading.Lock()               # защищает _sessions, _runs и _student_locks
_student_locks: dict[str, threading.Lock] = {}


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


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/info")
def info():
    """Режим работы: интерфейс показывает, какие агенты настоящие, а какие заглушки."""
    return {
        "checker": "real" if _real_checker else "stub",
        "tutor": "llm" if os.getenv("GIGACHAT_CREDENTIALS") else "stub",
        "max_steps": MAX_STEPS,
    }


@router.post("/chat")
def chat(req: ChatRequest):
    with _student_lock(req.student_id):
        with _state_lock:
            sess = _sessions.setdefault(req.student_id, {"task": None, "hint_level": 1, "history": []})
            task, hint_level, history = sess["task"], sess["hint_level"], list(sess["history"])
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
        with _state_lock:
            sess.update(
                task=out.get("next_task"),
                hint_level=out.get("next_hint_level", 1),
                history=out["history"][-50:],
            )
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
    }


@router.get("/session/{student_id}")
def session(student_id: str = Path(min_length=1, max_length=64)):
    """Состояние сессии: после обновления страницы интерфейс подтягивает отсюда диалог."""
    with _state_lock:
        sess = _sessions.get(student_id)
        if sess is None:
            return {"student_id": student_id, "hint_level": 1, "task": None, "history": []}
        return {
            "student_id": student_id,
            "hint_level": sess["hint_level"],
            "task": _dump(sess["task"]),
            "history": list(sess["history"]),
        }


@router.post("/reset/{student_id}")
def reset(student_id: str = Path(min_length=1, max_length=64)):
    with _state_lock:
        _sessions.pop(student_id, None)
    return {"reset": student_id}


@router.get("/metrics")
def metrics():
    with _state_lock:
        runs = list(_runs)
    return summarize(runs)
