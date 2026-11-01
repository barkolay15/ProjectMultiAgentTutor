"""Тесты чистых функций интерфейса (без streamlit и сети).
Запуск из корня проекта: python -m tests.test_frontend_helpers (или pytest)
"""
from frontend.helpers import (
    api_error_text,
    category_label,
    chat_to_markdown,
    clamp_hint,
    hint_text,
    history_to_chat,
    pipeline_line,
    slowest_step,
    timings_table,
    verdict_info,
)


def test_labels_have_fallbacks():
    assert category_label("edge_case") == "граничный случай"
    assert category_label("something_new") == "something_new"
    assert category_label(None) == "не определена"


def test_hint_is_clamped_to_range():
    assert clamp_hint(0) == 1 and clamp_hint(7) == 3 and clamp_hint("2") == 2 and clamp_hint(None) == 1
    assert hint_text(2) == "Подсказка 2 из 3"


def test_verdict_info():
    assert verdict_info(None) is None
    assert verdict_info({"passed": True})["kind"] == "pass"
    bad = verdict_info({"passed": False})
    assert bad["kind"] == "fail" and bad["color"] == "red"


def test_timings_table_shares_sum_to_about_100():
    rows = timings_table([{"node": "check", "seconds": 3}, {"node": "tutor", "seconds": 1}])
    assert [r["Шаг"] for r in rows] == ["Проверка кода", "Ответ тьютора"]
    assert [r["Доля, %"] for r in rows] == [75, 25]
    assert timings_table([]) == []
    assert timings_table([{"node": "check", "seconds": 0}])[0]["Доля, %"] == 0


def test_slowest_step():
    assert slowest_step([]) is None
    assert slowest_step([{"node": "check", "seconds": 2}, {"node": "tutor", "seconds": 1}]).startswith("Проверка кода")


def test_pipeline_line_skips_finish():
    line = pipeline_line([{"node": "check", "seconds": 0.5}, {"node": "tutor", "seconds": 1.25},
                          {"node": "finish", "seconds": 0.0}])
    assert line == "Проверка кода 0.50 с → Ответ тьютора 1.25 с"
    assert pipeline_line(None) == ""


def test_api_error_text():
    assert "2000" in api_error_text(422)
    assert "Подробности" in api_error_text(500, "Оркестратор не смог")
    assert "404" not in api_error_text(404) and "адрес" in api_error_text(404).lower()
    assert "418" in api_error_text(418)


def test_history_to_chat_maps_roles_and_skips_unknown():
    chat = history_to_chat([
        {"role": "student", "text": "привет"},
        {"role": "tutor", "text": "здравствуйте"},
        {"role": "system", "text": "служебное"},
    ])
    assert chat == [{"role": "user", "text": "привет"}, {"role": "assistant", "text": "здравствуйте"}]
    assert history_to_chat(None) == []


def test_chat_to_markdown_contains_code_and_verdict():
    md = chat_to_markdown([
        {"role": "user", "text": "Проверьте", "code": "def f(): return 0"},
        {"role": "assistant", "text": "Тест не прошёл.",
         "details": {"verdict": {"passed": False}, "hint_level": 2}},
    ], "s1")
    assert md.startswith("# Диалог с репетитором (студент s1)")
    assert "```python\ndef f(): return 0\n```" in md
    assert "Тесты не прошли" in md and "подсказка 2 из 3" in md
    assert md.endswith("\n")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)