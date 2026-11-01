"""Замер оркестратора: latency p50 и p95, error rate, доля сессий без падения.

Запуск из корня проекта:
    python3 -m benchmarks.eval_orchestrator                 # заглушки: меряем накладные расходы графа
    python3 -m benchmarks.eval_orchestrator --real-checker  # с настоящим проверяющим (нужен ключ в .env)
    python3 -m benchmarks.eval_orchestrator --n 10 --pause 1.0

Результат: таблица в консоли и файл benchmarks/results/orchestrator_<режим>_<время>.json.
"""
import argparse
import json
from datetime import datetime
from pathlib import Path
import time

from core.metrics import summarize
from core.orchestrator import run_turn
from core.stubs import stub_agents

RESULTS = Path(__file__).parent / "results"
CODES = [None, "def strlen(s): return 0", "def strlen(s): return len(s)"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30, help="число сессий")
    ap.add_argument("--pause", type=float, default=0.0, help="пауза между сессиями, с")
    ap.add_argument("--real-checker", action="store_true")
    a = ap.parse_args()

    agents = stub_agents()
    mode = "stub"
    if a.real_checker:
        from core.adapters import with_real_checker
        from core.llm_client import get_llm
        agents, mode = with_real_checker(agents, get_llm()), "real_checker"

    rows = []
    for i in range(a.n):
        out = run_turn(f"s{i}", "помоги с задачей", code=CODES[i % len(CODES)], agents=agents)
        rows.append({"session": i, "seconds": out["total_seconds"], "status": out["status"],
                     "retries": out.get("retries", 0), "errors": out.get("errors", []),
                     "timings": out["timings"]})
        print(f"[{i + 1}/{a.n}] {out['status']} {out['total_seconds']} с")
        if a.pause:
            time.sleep(a.pause)

    summary = summarize(rows)
    print(f"\n=== Оркестратор, режим {mode}: {summary} ===")
    RESULTS.mkdir(exist_ok=True)
    out_file = RESULTS / f"orchestrator_{mode}_{datetime.now():%Y%m%d_%H%M%S}.json"
    out_file.write_text(json.dumps({"meta": {"mode": mode, "time": datetime.now().isoformat(timespec="seconds")},
                                    "summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"Подробности: {out_file}")


if __name__ == "__main__":
    main()