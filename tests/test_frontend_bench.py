"""Совместимость страницы замеров с тремя режимами и неполными результатами."""
import json
import runpy
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

PAGE = Path(__file__).resolve().parents[1] / "frontend" / "pages" / "1_Замеры.py"


@pytest.fixture
def bench(tmp_path, monkeypatch):
    monkeypatch.setenv("TUTOR_RESULTS_DIR", str(tmp_path))
    return runpy.run_path(str(PAGE))


def save_run(folder, mode, *, errors=0, total=3, variant="one"):
    data = {"meta": {"mode": mode, "time": "2026-11-09T12:00:00", "prompt_version": "v7"},
            "summary": {"n_cases": total, "n_errors": errors, "category_accuracy": 0.5,
                        "accuracy_by_type": {"logic": 0.5},
                        "confusion": {"logic": {"logic": 1, "нет разбора": 1}}},
            "rows": [{"task_id": "task/one", "variant_id": variant, "expected": "logic"}]}
    (folder / f"checker_{mode}.json").write_text(json.dumps(data), encoding="utf-8")


def test_confusion_includes_cases_without_analysis(bench):
    table = bench["confusion_table"]({"summary": {"confusion": {
        "logic": {"logic": 2, "нет разбора": 3}}}})
    assert table.loc["логика", "нет разбора"] == 3
    assert table.to_numpy().sum() == 5


def test_load_skips_invalid_reports_and_keeps_three_modes(bench, tmp_path):
    for mode in ("baseline", "plain_llm", "llm"):
        save_run(tmp_path, mode)
    for name, content in (("list", "[]"), ("empty", '{"meta": null, "summary": {}}'), ("broken", "{")):
        (tmp_path / f"checker_bad_{name}.json").write_text(content, encoding="utf-8")
    runs = bench["load_runs"](tmp_path)
    assert {run["meta"]["mode"] for run in runs} == {"baseline", "plain_llm", "llm"}


def test_page_shows_failures_and_warns_about_different_cases(bench, tmp_path):
    save_run(tmp_path, "baseline")
    save_run(tmp_path, "plain_llm", errors=1, variant="different")
    save_run(tmp_path, "llm")
    app = AppTest.from_file(str(PAGE), default_timeout=10).run()
    assert not app.exception
    table = app.dataframe[0].value.set_index("Показатель")
    assert table.loc["Сбоев прогона", "GigaChat без обвязки"] == "1"
    assert any("Наборы мутантов" in item.value for item in app.warning)
    assert any("не входят в расчёт точности" in item.value for item in app.warning)
    assert "нет разбора" in app.dataframe[1].value.columns


def test_zero_case_report_does_not_crash(bench, tmp_path):
    save_run(tmp_path, "llm", total=0)
    app = AppTest.from_file(str(PAGE), default_timeout=10).run()
    assert not app.exception
    assert any("нет случаев без сбоев" in item.value for item in app.info)


def test_empty_page_lists_all_three_commands(bench):
    app = AppTest.from_file(str(PAGE), default_timeout=10).run()
    assert not app.exception
    assert any("--mode plain_llm" in item.value for item in app.code)
