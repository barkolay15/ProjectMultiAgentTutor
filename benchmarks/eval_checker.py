"""Замер качества агента «Проверяющий» на мутантах (сломанных решениях с известным типом поломки).

Запуск из корня проекта:
    python3 -m benchmarks.eval_checker                     # настоящая модель GigaChat (нужен ключ в .env)
    python3 -m benchmarks.eval_checker --mode baseline     # без LLM: тип ошибки по правилам из вердикта
    python3 -m benchmarks.eval_checker --limit 10 --pause 1.0

Данные: benchmarks/datasets/checker_set.json (эталоны и мутанты с bug_type) и задачи из
benchmarks/datasets. Результат: таблица в консоли и файл benchmarks/results/checker_<режим>_<время>.json.

Метрики (считаем только на мутантах, вердикт «не прошёл» известен заранее):
  category_accuracy   доля случаев, где category из разбора совпала с bug_type
  json_valid_first    доля случаев, где модель с первой попытки вернула JSON по схеме
  no_code_first       доля случаев, где первый ответ был по схеме и без кода в тексте
  analysis_ok         доля случаев, где итоговый разбор получен (после повторов)
  tool_called         доля случаев, где модель сама вызвала run_tests
  latency_s, llm_calls   среднее время проверки и число обращений к модели
Базовая линия (baseline) показывает, что даёт простое правило без модели: по нему легко узнать
syntax, runtime, timeout, а logic от edge_case отличить нельзя.
"""
import argparse
import json
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from agents.checker.prompts import PROMPT_VERSION
from agents.checker.agent import analyze
from agents.checker.sandbox import DATASETS, check, load_tasks
from schemas.messages import CheckRequest, CheckVerdict

RESULTS = Path(__file__).parent / "results"
CATEGORIES = ["syntax", "logic", "edge_case", "runtime", "timeout"]


def baseline_category(verdict: CheckVerdict) -> str:
    """Правило без модели: тип ошибки из вердикта -> category."""
    et = verdict.error_type or ""
    if et in ("SyntaxError", "IndentationError", "TabError"):
        return "syntax"
    if et in ("Timeout", "RuntimeCrash"):
        return "timeout"
    if et == "AssertionError":
        return "logic"           # отличить edge_case по вердикту нельзя
    return "runtime"


def evaluate(items: list[dict], tasks: dict, mode: str = "llm", llm=None,
             limit: int | None = None, pause: float = 0.0, log=print) -> list[dict]:
    """Прогоняет мутантов, возвращает по строке на каждого."""
    cases = [(it["task_id"], m) for it in items for m in it["mutants"]]
    if limit:
        cases = cases[:limit]
    rows = []
    for i, (tid, m) in enumerate(cases, 1):
        row = {"variant_id": m["variant_id"], "task_id": tid, "expected": m["bug_type"],
               "predicted": None, "error": None}
        req = CheckRequest(task_id=tid, student_code=m["code"])
        t0 = time.time()
        try:
            if mode == "baseline":
                verdict = check(req, tasks)
                row.update(predicted=baseline_category(verdict), llm_calls=0, tool_called_by_model=None,
                           json_valid_first_try=None, no_code_first_try=None, analysis_ok=None)
            else:
                stats: dict = {}
                verdict, analysis = analyze(req, llm, stats)
                row.update(predicted=analysis.category if analysis else None,
                           summary=analysis.summary if analysis else None,
                           hint_for_tutor=analysis.hint_for_tutor if analysis else None,
                           analysis_ok=analysis is not None, **stats)
            row["verdict_error_type"] = verdict.error_type
        except Exception as e:      # сбой сети/лимит API не должен терять уже полученные результаты
            row["error"] = f"{type(e).__name__}: {e}"[:300]
        row["latency_s"] = round(time.time() - t0, 2)
        rows.append(row)
        log(f"[{i}/{len(cases)}] {row['variant_id']}: ждали {row['expected']}, "
            f"получили {row['predicted']}{' (ОШИБКА: ' + row['error'] + ')' if row['error'] else ''}")
        if pause and mode != "baseline":
            time.sleep(pause)
    return rows


def _rate(values) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(1 for v in vals if v) / len(vals), 3) if vals else None


def summarize(rows: list[dict]) -> dict:
    ok_rows = [r for r in rows if not r.get("error")]
    per_type: dict[str, list] = defaultdict(list)
    confusion: dict[str, Counter] = defaultdict(Counter)
    for r in ok_rows:
        per_type[r["expected"]].append(r["predicted"] == r["expected"])
        confusion[r["expected"]][r["predicted"] or "нет разбора"] += 1
    lat = [r["latency_s"] for r in ok_rows]
    calls = [r["llm_calls"] for r in ok_rows if r.get("llm_calls") is not None]
    return {
        "n_cases": len(rows),
        "n_errors": len(rows) - len(ok_rows),
        "category_accuracy": _rate([r["predicted"] == r["expected"] for r in ok_rows]),
        "accuracy_by_type": {t: round(sum(v) / len(v), 3) for t, v in per_type.items()},
        "json_valid_first": _rate([r.get("json_valid_first_try") for r in ok_rows]),
        "no_code_first": _rate([r.get("no_code_first_try") for r in ok_rows]),
        "analysis_ok": _rate([r.get("analysis_ok") for r in ok_rows]),
        "tool_called": _rate([r.get("tool_called_by_model") for r in ok_rows]),
        "latency_s_mean": round(sum(lat) / len(lat), 2) if lat else None,
        "llm_calls_mean": round(sum(calls) / len(calls), 2) if calls else None,
        "confusion": {e: dict(c) for e, c in confusion.items()},
    }


def print_report(summary: dict, mode: str):
    print(f"\n=== Режим: {mode}, промпт {PROMPT_VERSION}: {summary['n_cases']} мутантов, "
          f"сбоев: {summary['n_errors']} ===")
    for key in ("category_accuracy", "json_valid_first", "no_code_first", "analysis_ok",
                "tool_called", "latency_s_mean", "llm_calls_mean"):
        print(f"{key:20} {summary[key]}")
    print("точность по типам:", summary["accuracy_by_type"])
    print("ожидали -> получили:")
    for e in CATEGORIES:
        if e in summary["confusion"]:
            print(f"  {e:10} {summary['confusion'][e]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["llm", "baseline"], default="llm")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--pause", type=float, default=0.3, help="пауза между запросами к модели, с")
    a = ap.parse_args()

    path = DATASETS / "checker_set.json"
    if not path.exists():
        raise SystemExit(f"Нет файла {path}: его делает Олег (формат в README_для_Олега.md).")
    items = json.loads(path.read_text(encoding="utf-8"))
    tasks = load_tasks()
    llm = None
    if a.mode == "llm":
        from core.llm_client import get_llm
        llm = get_llm()

    rows = evaluate(items, tasks, a.mode, llm, a.limit, a.pause)
    summary = summarize(rows)
    print_report(summary, a.mode)

    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"checker_{a.mode}_{datetime.now():%Y%m%d_%H%M%S}.json"
    meta = {"mode": a.mode, "prompt_version": PROMPT_VERSION, "model": getattr(llm, "model", None),
            "time": datetime.now().isoformat(timespec="seconds")}
    out.write_text(json.dumps({"meta": meta, "summary": summary, "rows": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nПодробности: {out}")


if __name__ == "__main__":
    main()
