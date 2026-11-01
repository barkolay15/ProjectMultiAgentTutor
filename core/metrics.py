"""Метрики оркестратора: latency p50 и p95, error rate, доля сессий без падения."""


def percentile(values: list[float], p: float) -> float:
    """Перцентиль с линейной интерполяцией; p от 0 до 100."""
    if not values:
        return 0.0
    xs = sorted(values)
    k = (len(xs) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def summarize(runs: list[dict]) -> dict:
    """runs: [{"seconds": float, "status": "ok" | "error" | "step_limit"}, ...]"""
    n = len(runs)
    if n == 0:
        return {"sessions": 0, "p50": 0.0, "p95": 0.0, "error_rate": 0.0, "ok_rate": 0.0}
    seconds = [r["seconds"] for r in runs]
    bad = sum(1 for r in runs if r["status"] != "ok")
    return {
        "sessions": n,
        "p50": round(percentile(seconds, 50), 4),
        "p95": round(percentile(seconds, 95), 4),
        "error_rate": round(bad / n, 4),
        "ok_rate": round(1 - bad / n, 4),
    }