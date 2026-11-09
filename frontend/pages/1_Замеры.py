"""Страница «Замеры»: правило, голая модель и проверяющий с тестами.

Страница подключена к меню слева через st.navigation в frontend/app.py.
Данные берутся из benchmarks/results/checker_*.json (папка в .gitignore: сначала запустите замер).
Другую папку можно задать переменной TUTOR_RESULTS_DIR.
"""
import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

RESULTS_DIR = Path(os.getenv("TUTOR_RESULTS_DIR")
                   or Path(__file__).resolve().parents[2] / "benchmarks" / "results")

MODE_NAMES = {"baseline": "Правило без модели", "plain_llm": "GigaChat без обвязки", "llm": "GigaChat + тесты и проверки"}
TYPE_ORDER = ["syntax", "logic", "edge_case", "runtime", "timeout", "other"]
TYPE_NAMES = {
    "syntax": "синтаксис", "logic": "логика", "edge_case": "граничный случай",
    "runtime": "ошибка выполнения", "timeout": "зависание", "other": "другое",
}
# (ключ в summary, подпись, процент?)
METRICS = [
    ("n_cases", "Всего мутантов", False),
    ("n_errors", "Сбоев прогона", False),
    ("category_accuracy", "Точность по типам ошибок", True),
    ("json_valid_first", "Валидный JSON с первой попытки", True),
    ("no_code_first", "Без кода в ответе с первой попытки", True),
    ("analysis_ok", "Разбор ошибки прошёл проверку", True),
    ("tool_called", "Модель сама запустила тесты", True),
    ("latency_s_mean", "Время на один мутант, с", False),
    ("llm_calls_mean", "Вызовов модели на один мутант", False),
]


def load_runs(folder: Path = RESULTS_DIR) -> list[dict]:
    """Все файлы checker_*.json, новые первыми. Битые файлы пропускаем."""
    runs = []
    for path in Path(folder).glob("checker_*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            meta, summary = data["meta"], data["summary"]
            if not isinstance(meta, dict) or not isinstance(summary, dict) or meta.get("mode") not in MODE_NAMES:
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        rows = data.get("rows")
        cases = frozenset((row.get("task_id"), row["variant_id"], row.get("expected"))
                          for row in rows if isinstance(row, dict)
                          and all(isinstance(row.get(key), str) for key in ("task_id", "variant_id", "expected"))) \
            if isinstance(rows, list) else frozenset()
        runs.append({"path": path, "meta": meta, "summary": summary, "cases": cases})
    runs.sort(key=lambda r: r["meta"].get("time", ""), reverse=True)
    return runs


def run_label(run: dict) -> str:
    m, s = run["meta"], run["summary"]
    return f"{m.get('time', '?').replace('T', ' ')} · промпт {m.get('prompt_version', '?')} · {s.get('n_cases', '?')} мутантов"


def fmt(value, percent: bool) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    return f"{value * 100:.0f}%" if percent else f"{value:g}"


def comparison_table(base: dict | None, llm: dict | None, plain: dict | None = None) -> pd.DataFrame:
    rows = []
    for key, title, percent in METRICS:
        row = {"Показатель": title,
               MODE_NAMES["baseline"]: fmt(base["summary"].get(key), percent) if base else "—"}
        row[MODE_NAMES["plain_llm"]] = fmt(plain["summary"].get(key), percent) if plain else "—"
        row[MODE_NAMES["llm"]] = fmt(llm["summary"].get(key), percent) if llm else "—"
        rows.append(row)
    return pd.DataFrame(rows)


def accuracy_by_type(base: dict | None, llm: dict | None, plain: dict | None = None) -> pd.DataFrame:
    """Строки: типы ошибок, которые есть хотя бы в одном прогоне. Значения в процентах."""
    data = {}
    for mode, run in (("baseline", base), ("plain_llm", plain), ("llm", llm)):
        if run:
            data[MODE_NAMES[mode]] = run["summary"].get("accuracy_by_type", {})
    types = [t for t in TYPE_ORDER if any(t in d for d in data.values())]
    return pd.DataFrame({name: [round(d[t] * 100) if t in d else None for t in types]
                         for name, d in data.items()},
                        index=[TYPE_NAMES[t] for t in types])


def confusion_table(run: dict) -> pd.DataFrame:
    """Строки: что ждали, столбцы: что получили."""
    conf = run["summary"].get("confusion", {})
    expected = [t for t in TYPE_ORDER if t in conf] + sorted(set(conf) - set(TYPE_ORDER))
    predictions = {t for e in expected for t in conf[e]}
    got = [t for t in TYPE_ORDER if t in predictions] + sorted(predictions - set(TYPE_ORDER))
    table = pd.DataFrame([[conf[e].get(g, 0) for g in got] for e in expected],
                         index=[TYPE_NAMES.get(e, e) for e in expected],
                         columns=[TYPE_NAMES.get(g, g) for g in got])
    table.index.name = "Ждали ↓ / получили →"
    return table


def pick(runs: list[dict], mode: str, label: str):
    """Выбор прогона нужного режима; по умолчанию самый свежий."""
    of_mode = [r for r in runs if r["meta"].get("mode") == mode]
    if not of_mode:
        st.info(f"Прогонов режима «{MODE_NAMES[mode]}» пока нет.")
        return None
    labels = [run_label(r) for r in of_mode]
    chosen = st.selectbox(label, labels, key=f"pick_{mode}")
    return of_mode[labels.index(chosen)]


def main() -> None:
    st.set_page_config(page_title="Замеры проверяющего", page_icon="📊", layout="wide")
    st.title("Замеры проверяющего")
    st.caption("Проверяющий определяет тип поломки в решении студента. Сравниваем простое правило без модели, "
               "GigaChat без обвязки и GigaChat с тестами и проверками на сломанных решениях (мутантах).")

    runs = load_runs()
    if not runs:
        st.warning("Результатов пока нет. Запустите замер из корня репозитория и обновите страницу:")
        st.code("python3 -m benchmarks.eval_checker --mode baseline\n"
                "python3 -m benchmarks.eval_checker --mode plain_llm --pause 2\n"
                "python3 -m benchmarks.eval_checker --mode llm --pause 2", language="bash")
        return

    c1, c2, c3 = st.columns(3)
    with c1:
        base = pick(runs, "baseline", "Прогон без модели")
    with c2:
        plain = pick(runs, "plain_llm", "Прогон GigaChat без обвязки")
    with c3:
        llm = pick(runs, "llm", "Прогон GigaChat с проверяющим")

    st.subheader("Сравнение")
    st.dataframe(comparison_table(base, llm, plain), hide_index=True)
    st.caption("Точность рассчитана по случаям без сбоев; сбои указаны отдельно.")

    selected = [r for r in (base, plain, llm) if r]
    n_values = [r["summary"]["n_cases"] for r in selected
                if type(r["summary"].get("n_cases")) is int]
    case_sets = [r["cases"] for r in selected if r.get("cases")]
    if len(set(n_values)) > 1 or (case_sets and any(cases != case_sets[0] for cases in case_sets[1:])):
        st.warning("Наборы мутантов в выбранных прогонах различаются. Для сравнения запустите режимы на одном наборе.")
    if any(r["summary"].get("n_errors", 0) for r in selected):
        st.warning("В выбранных прогонах есть сбои. Они не входят в расчёт точности.")
    usable = [r["summary"]["n_cases"] - r["summary"].get("n_errors", 0) for r in selected
              if type(r["summary"].get("n_cases")) is int
              and type(r["summary"].get("n_errors", 0)) is int]
    if usable and min(usable) < 30:
        n = min(usable)
        if n > 0:
            st.caption(f"Случаев без сбоев мало ({n}): один промах меняет точность примерно на {100 / n:.0f}%. "
                       "Это не окончательный результат.")
        else:
            st.info("В выбранном прогоне нет случаев без сбоев для расчёта точности.")

    by_type = accuracy_by_type(base, llm, plain)
    if not by_type.empty:
        st.subheader("Точность по типам ошибок, %")
        try:
            st.bar_chart(by_type, stack=False)   # столбцы рядом, а не друг на друге (иначе сумма до 200%)
        except TypeError:                        # старая версия Streamlit без параметра stack
            st.bar_chart(by_type)

    if llm and llm["summary"].get("confusion"):
        st.subheader("Что ждали и что получили (GigaChat)")
        st.dataframe(confusion_table(llm))

    with st.expander(f"Все прогоны ({len(runs)})"):
        st.dataframe(pd.DataFrame([{
            "Время": r["meta"].get("time", "").replace("T", " "),
            "Режим": MODE_NAMES.get(r["meta"].get("mode"), r["meta"].get("mode")),
            "Промпт": r["meta"].get("prompt_version"),
            "Мутантов": r["summary"].get("n_cases"),
            "Сбоев": r["summary"].get("n_errors"),
            "Точность": fmt(r["summary"].get("category_accuracy"), True),
            "Файл": r["path"].name,
        } for r in runs]), hide_index=True)


if __name__ == "__main__":
    main()
