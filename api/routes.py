"""HTTP-ручки: интерфейс отправляет сюда сообщения, оркестратор отвечает."""
import os

from dotenv import load_dotenv

load_dotenv()

from fastapi import APIRouter
from pydantic import BaseModel

from core.metrics import summarize
from core.orchestrator import Agents, run_turn
from core.stubs import stub_agents

router = APIRouter()

# По умолчанию заглушки. USE_REAL_CHECKER=1 подключает настоящего проверяющего (нужен ключ в .env).
_agents: Agents = stub_agents()
if os.getenv("USE_REAL_CHECKER") == "1":
    from core.adapters import with_real_checker
    from core.llm_client import get_llm
    _agents = with_real_checker(_agents, get_llm())
_sessions: dict[str, dict] = {}   # student_id -> {"task", "hint_level", "history"}
_runs: list[dict] = []            # для /metrics


def set_agents(agents: Agents) -> None:
    global _agents
    _agents = agents


class ChatRequest(BaseModel):
    student_id: str
    message: str
    code: str | None = None


def _dump(obj):
    return obj.model_dump() if obj is not None else None


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/chat")
def chat(req: ChatRequest):
    sess = _sessions.setdefault(req.student_id, {"task": None, "hint_level": 1, "history": []})
    out = run_turn(
        student_id=req.student_id,
        message=req.message,
        code=req.code,
        task=sess["task"],
        hint_level=sess["hint_level"],
        history=sess["history"],
        agents=_agents,
    )
    sess.update(
        task=out.get("next_task"),
        hint_level=out.get("next_hint_level", 1),
        history=out["history"][-50:],
    )
    _runs.append({"seconds": out["total_seconds"], "status": out["status"]})
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


@router.post("/reset/{student_id}")
def reset(student_id: str):
    _sessions.pop(student_id, None)
    return {"reset": student_id}


@router.get("/metrics")
def metrics():
    return summarize(_runs)