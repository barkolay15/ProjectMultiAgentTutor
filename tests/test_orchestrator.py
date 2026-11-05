"""Тесты оркестратора: без ключей и без сети (агенты это заглушки).
Запуск из корня проекта: python3 -m tests.test_orchestrator (или pytest)
"""
import dataclasses

from core.metrics import percentile, summarize
from core.orchestrator import FALLBACK_MESSAGE, run_turn, stream_turn
from core.stubs import stub_agents
from schemas.messages import StudentProfile

GOOD = "def strlen(s): return len(s)"
BAD = "def strlen(s): return 0"


def nodes(out):
    return [t["node"] for t in out["timings"]]


def test_correct_code_passes_and_task_stays_open():
    out = run_turn("s1", "проверьте", code=GOOD)
    assert out["status"] == "ok"
    assert out["verdict"].passed
    assert out.get("analysis") is None            # ревью не нужно
    assert out["memory_event"].result == "pass"
    assert out["next_task"] == out["task"]        # можно обсудить и перепроверить решение
    assert out["next_hint_level"] == 1


def test_failed_code_goes_through_review_and_raises_hint_level():
    out = run_turn("s1", "проверьте", code=BAD, hint_level=1)
    assert out["verdict"].passed is False
    assert out["analysis"] is not None            # сработало условное ребро в review
    assert out["memory_event"].result == "fail"
    assert out["next_hint_level"] == 2
    assert out["next_task"] is not None           # задача остаётся той же
    assert nodes(out)[:4] == ["load_profile", "pick_task", "check", "review"]


def test_no_code_skips_checker():
    out = run_turn("s1", "не понимаю задачу")
    assert out.get("verdict") is None
    assert out["memory_event"].result == "hint_used"
    assert "check" not in nodes(out)


def test_invalid_json_is_retried_with_error_text():
    agents = stub_agents()
    good_tutor = agents.tutor
    calls = []

    def flaky_tutor(req, err):
        calls.append(err)
        return "это не JSON" if len(calls) == 1 else good_tutor(req, err)

    out = run_turn("s1", "привет", agents=dataclasses.replace(agents, tutor=flaky_tutor))
    assert out["status"] == "ok"
    assert out["retries"] == 1
    assert calls[0] is None and "JSON" in calls[1]   # во второй раз агенту передали текст ошибки


def test_wrong_object_type_is_retried():
    agents = stub_agents()
    good_tutor = agents.tutor
    calls = []

    def tutor(req, err):
        calls.append(err)
        if len(calls) == 1:
            return StudentProfile(student_id="s1")   # объект не той схемы
        return good_tutor(req, err)

    out = run_turn("s1", "привет", agents=dataclasses.replace(agents, tutor=tutor))
    assert out["status"] == "ok" and out["retries"] == 1


def test_always_invalid_reply_gives_error_and_fallback():
    agents = dataclasses.replace(stub_agents(), tutor=lambda req, err: "мусор")
    out = run_turn("s1", "привет", agents=agents)
    assert out["status"] == "error"
    assert out["reply"].message == FALLBACK_MESSAGE
    assert out["errors"]


def test_agent_exception_is_caught():
    def boom(req, err):
        raise RuntimeError("сеть упала")

    out = run_turn("s1", "привет", agents=dataclasses.replace(stub_agents(), memory_get=boom))
    assert out["status"] == "error"
    assert "RuntimeError" in out["errors"][0]


def test_step_limit():
    out = run_turn("s1", "привет", max_steps=1)
    assert out["status"] == "step_limit"
    assert out["reply"].message == FALLBACK_MESSAGE


def test_percentile_and_summary():
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([], 95) == 0.0
    s = summarize([{"seconds": 1, "status": "ok"}, {"seconds": 3, "status": "error"}])
    assert s["error_rate"] == 0.5 and s["ok_rate"] == 0.5 and s["p50"] == 2.0


def test_review_failure_is_not_fatal():
    def no_analysis(payload, err):
        raise ValueError("проверяющий не дал разбор")

    out = run_turn("s1", "проверьте", code=BAD, agents=dataclasses.replace(stub_agents(), analyze=no_analysis))
    assert out["status"] == "ok"                  # тьютор всё равно ответил по вердикту
    assert out.get("analysis") is None
    assert out["errors"] and "review" in out["errors"][0]
    assert "tutor" in nodes(out)


def test_stream_turn_gives_steps_then_same_result_as_run_turn():
    events = list(stream_turn("s1", "проверьте", code=BAD))
    steps = [e["node"] for e in events if e["type"] == "step"]
    assert steps[:4] == ["load_profile", "pick_task", "check", "review"] and steps[-1] == "finish"
    assert events[-1]["type"] == "final"
    final = events[-1]["state"]
    plain = run_turn("s1", "проверьте", code=BAD)
    assert final["status"] == plain["status"] == "ok"
    assert final["verdict"].passed == plain["verdict"].passed is False
    assert final["next_hint_level"] == plain["next_hint_level"] == 2
    assert final["total_seconds"] >= 0


def test_tutor_receives_code_and_history_of_current_task():
    captured = []
    agents = stub_agents()
    original_tutor = agents.tutor

    def tutor(req, err):
        captured.append(req)
        return original_tutor(req, err)

    agents = dataclasses.replace(agents, tutor=tutor)
    first = run_turn("context", "Почему не работает?", code=BAD, agents=agents)
    assert captured[0].student_code == BAD
    assert first["history"][0]["code"] == BAD
    other_task = {"role": "student", "text": "Другая задача", "task_id": "other/task"}
    second = run_turn("context", "Объясни предыдущую подсказку", task=first["next_task"],
                      history=[other_task, *first["history"]], agents=agents)
    assert second["status"] == "ok"
    assert captured[1].history == first["history"]
    assert captured[1].history[0]["code"] == BAD
    assert captured[1].history[1]["text"] == first["reply"].message


def test_stubs_never_use_live_tutor_even_with_credentials(monkeypatch):
    from unittest.mock import Mock
    from core import llm_client

    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "test-placeholder")
    get_llm = Mock(side_effect=AssertionError("Заглушки не должны вызывать модель"))
    monkeypatch.setattr(llm_client, "get_llm", get_llm)
    assert run_turn("offline", "привет")["status"] == "ok"
    assert list(stream_turn("offline", "привет"))[-1]["state"]["status"] == "ok"
    get_llm.assert_not_called()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
