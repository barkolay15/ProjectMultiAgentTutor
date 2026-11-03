"""Проверка состояния интерфейса через Streamlit AppTest, без сети и LLM."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from frontend import client, editor

BANK = [
    {"task_id": "bank/one", "entry_point": "strlen", "statement": "Длина строки.",
     "starter": "def strlen(s):\n    pass", "example": {"input": "'abc'", "output": "3"}},
    {"task_id": "bank/two", "entry_point": "double", "statement": "Удвоить число.",
     "starter": "def double(n):\n    pass", "example": {"input": "2", "output": "4"}},
]
CODE = "def strlen(s):\n    return 0"


@pytest.fixture
def ui(monkeypatch):
    api = Mock()
    api.health.return_value = True
    api.info.return_value = {"checker": "real", "tutor": "llm", "bank": 2,
                             "model": "GigaChat-2", "checker_prompt_version": "v4"}
    api.tasks.return_value = deepcopy(BANK)
    api.session.return_value = {"task": BANK[0], "hint_level": 1, "solved": [], "history": []}
    api.metrics.return_value = None

    def set_task(student, task_id):
        task = next(t for t in BANK if t["task_id"] == task_id)
        return {"task": task, "starter": task["starter"], "hint_level": 1}

    def chat_stream(student, message, code):
        verdict = None if code is None else {
            "passed": False, "failed_details": [{"test": "assert strlen('abc') == 3", "got": "0"}],
            "failed_tests": ["assert strlen('abc') == 3"], "error_type": "AssertionError",
        }
        return iter([{"type": "result", "data": {
            "reply": "Подумайте о пустой строке.", "task": BANK[0], "hint_level": 2,
            "verdict": verdict, "analysis": None, "status": "ok", "total_seconds": 0.2,
            "solved": [], "timings": [], "errors": [],
        }}])

    api.set_task.side_effect = set_task
    api.chat_stream.side_effect = chat_stream
    monkeypatch.setattr(client, "TutorClient", Mock(return_value=api))
    monkeypatch.setattr(editor, "st_ace", None)
    st.cache_data.clear()
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "frontend" / "app.py"), default_timeout=10)
    yield app, api
    st.cache_data.clear()


def test_drafts_survive_task_changes_and_are_separate_for_students(ui):
    app, _ = ui
    app.run()
    assert not app.exception
    app.text_area[0].input(CODE).run()
    app.button(key="next_task").click().run()
    assert app.text_area[0].value == BANK[1]["starter"]
    app.text_area[0].input("def double(n):\n    return n * 2").run()
    app.button(key="next_task").click().run()
    assert not app.exception
    assert app.text_area[0].value == CODE
    app.text_input(key="student_id").input("s2").run()
    assert app.text_area[0].value == BANK[0]["starter"]
    app.text_area[0].input("# другой студент").run()
    app.text_input(key="student_id").input("s1").run()
    assert app.text_area[0].value == CODE


def test_reset_code_replaces_only_current_draft(ui):
    app, _ = ui
    app.run().text_area[0].input(CODE).run()
    app.button(key="next_task").click().run()
    app.text_area[0].input("# второй черновик").run()
    app.button(key="reset_code").click().run()
    assert app.text_area[0].value == BANK[1]["starter"]
    app.button(key="next_task").click().run()
    assert app.text_area[0].value == CODE


def test_result_survives_hint_and_marks_changed_code(ui):
    app, _ = ui
    app.run().text_area[0].input(CODE).run()
    app.button(key="run").click().run()
    assert not app.exception
    assert any("Тесты не прошли" in item.value for item in app.error)
    assert any(item.value == "Ожидалось" for item in app.caption)
    assert any(item.value == "Получено" for item in app.caption)
    app.button(key="hint").click().run()
    assert any("Тесты не прошли" in item.value for item in app.error)
    app.text_area[0].input(CODE + "\n# изменение").run()
    assert any("Код изменён" in item.value for item in app.info)


def test_service_error_keeps_code_and_retry_does_not_duplicate_chat(ui):
    app, api = ui
    success = api.chat_stream.side_effect
    api.chat_stream.side_effect = client.ApiError("Связь прервалась")
    app.run().text_area[0].input(CODE).run()
    app.button(key="run").click().run()
    assert not app.exception
    assert app.text_area[0].value == CODE
    assert any("Не удалось выполнить проверку" in item.value for item in app.warning)
    assert not any("Тесты не прошли" in item.value for item in app.error)
    api.chat_stream.side_effect = success
    app.button(key="retry_request").click().run()
    assert not app.exception
    assert api.chat_stream.call_args.args[2] == CODE
    assert len(app.session_state["chats"]["s1"]) == 2
    assert "s1" not in app.session_state["failed_requests"]


def test_failed_session_reset_keeps_progress_and_draft(ui):
    app, api = ui
    app.run().text_area[0].input(CODE).run()
    api.reset.side_effect = client.ApiError("Сервис недоступен")
    app.button(key="confirm_reset").click().run()
    assert not app.exception
    assert app.text_area[0].value == CODE
    assert app.session_state["last"]["s1"]["task"]["task_id"] == BANK[0]["task_id"]


def test_incomplete_turn_shows_real_cause(ui):
    app, api = ui
    api.chat_stream.side_effect = lambda *args: iter([{"type": "result", "data": {
        "reply": "Что-то пошло не так", "task": BANK[0], "status": "error", "hint_level": 1,
        "verdict": None, "errors": ["check: ConnectTimeout: unavailable"], "total_seconds": 0.2,
    }}])
    app.run().text_area[0].input(CODE).run()
    app.button(key="run").click().run()
    assert not app.exception
    assert any("GigaChat" in item.value for item in app.markdown)
    assert any("ConnectTimeout" in item.value for item in app.code)
    assert app.text_area[0].value == CODE


def test_unavailable_analysis_does_not_hide_test_verdict(ui):
    app, api = ui
    original = api.chat_stream.side_effect

    def stream(*args):
        events = list(original(*args))
        events[0]["data"]["errors"] = ["review: разбор ошибки недоступен: ConnectTimeout"]
        return iter(events)

    api.chat_stream.side_effect = stream
    app.run().text_area[0].input(CODE).run()
    app.button(key="run").click().run()
    assert not app.exception
    assert any("Тесты не прошли" in item.value for item in app.error)
    assert any("разбор ошибки от модели недоступен" in item.value for item in app.warning)
    assert not any(button.key == "retry_request" for button in app.button)


def test_initial_screen_guides_to_task_without_empty_editor(ui):
    app, api = ui
    api.session.return_value = {"task": None, "history": [], "solved": []}
    app.run()
    assert not app.exception
    assert not app.text_area
    assert not any(button.key == "run" for button in app.button)
    assert app.button(key="open_tasks_start")
    assert any("Давайте разберём задачу" in item.value for item in app.markdown)
    api.chat_stream.assert_not_called()
    app.button(key="start_first").click().run()
    assert not app.exception
    api.set_task.assert_called_once_with("s1", BANK[0]["task_id"])
    assert app.text_area[0].value == BANK[0]["starter"]


def test_catalog_previews_task_before_confirmation(ui):
    app, api = ui
    api.session.return_value = {"task": None, "history": [], "solved": []}
    app.run().button(key="open_tasks_start").click().run()
    assert not app.exception
    api.set_task.assert_not_called()
    choice = next(r for r in app.radio if str(r.key).startswith("task_choice_"))
    choice.set_value(BANK[1]["task_id"]).run()
    assert not app.exception
    api.set_task.assert_not_called()
    assert any("Удвоить число" in item.value for item in app.markdown)
    app.button(key="confirm_task").click().run()
    assert not app.exception
    api.set_task.assert_called_once_with("s1", BANK[1]["task_id"])
    assert app.text_area[0].value == BANK[1]["starter"]


def test_catalog_empty_search_does_not_change_current_task(ui):
    app, api = ui
    app.run().button(key="open_tasks").click().run()
    app.text_input(key="task_query").input("nothing-matches").run()
    assert not app.exception
    assert any("По вашему запросу задач нет" in item.value for item in app.info)
    api.set_task.assert_not_called()


def test_initial_screen_without_bank_can_request_a_task(ui):
    app, api = ui
    api.session.return_value = {"task": None, "history": [], "solved": []}
    api.info.return_value["checker"] = "stub"
    app.run()
    assert not app.exception
    assert app.button(key="get_first_task")
    assert not app.text_area
    app.button(key="get_first_task").click().run()
    assert not app.exception
    api.chat_stream.assert_called_once()
    assert app.text_area


def test_catalog_failure_preserves_current_draft(ui):
    app, api = ui
    app.run().text_area[0].input(CODE).run()
    app.button(key="open_tasks").click().run()
    choice = next(r for r in app.radio if str(r.key).startswith("task_choice_"))
    choice.set_value(BANK[1]["task_id"]).run()
    api.set_task.side_effect = client.ApiError("Не удалось выбрать задачу")
    app.button(key="confirm_task").click().run()
    assert not app.exception
    assert app.session_state["last"]["s1"]["task"]["task_id"] == BANK[0]["task_id"]
    assert app.session_state["drafts"]["s1"][BANK[0]["task_id"]] == CODE
    assert any("Не удалось выбрать задачу" in item.value for item in app.error)


def test_public_example_is_readable_in_task_and_catalog(ui):
    app, _ = ui
    app.run()
    assert not app.exception
    assert any(item.value == "Входные данные" for item in app.caption)
    assert any(item.value == "Ожидаемый результат" for item in app.caption)
    assert any(item.value == "'abc'" for item in app.code)
    assert not any("assert " in item.value for item in app.code)
    app.button(key="open_tasks").click().run()
    assert not app.exception
    assert sum(item.value == "Входные данные" for item in app.caption) == 2
    assert not any("assert " in item.value for item in app.code)
