"""Monte Carlo: бутстреп R-серии сделок (1000 путей).

Отвечает на вопрос: насколько результат зависит от порядка/состава сделок.
Метрики: распределение итогового R, max drawdown (в R), вероятность
прибыльности, 5/50/95 перцентили. Дополнительно — бутстреп-тест
H0: E[R] <= 0 (доля ресемплов со средним <= 0 ≈ p-value).
"""
from __future__ import annotations

import numpy as np


def monte_carlo(rs: list[float], n_paths: int = 1000, seed: int = 42) -> dict:
    if len(rs) < 10:
        return {"valid": False, "reason": f"слишком мало сделок ({len(rs)})"}
    rng = np.random.default_rng(seed)
    arr = np.array(rs)
    n = len(arr)

    totals = np.empty(n_paths)
    max_dds = np.empty(n_paths)
    means = np.empty(n_paths)
    for p in range(n_paths):
        sample = rng.choice(arr, size=n, replace=True)
        eq = np.cumsum(sample)
        peak = np.maximum.accumulate(eq)
        max_dds[p] = float((peak - eq).max())
        totals[p] = eq[-1]
        means[p] = sample.mean()

    p_value = float((means <= 0).mean())   # бутстреп p-value для E[R]>0
    return {
        "valid": True,
        "n_trades": n,
        "total_r_p5": round(float(np.percentile(totals, 5)), 2),
        "total_r_p50": round(float(np.percentile(totals, 50)), 2),
        "total_r_p95": round(float(np.percentile(totals, 95)), 2),
        "max_dd_r_p50": round(float(np.percentile(max_dds, 50)), 2),
        "max_dd_r_p95": round(float(np.percentile(max_dds, 95)), 2),
        "prob_profit": round(float((totals > 0).mean()), 3),
        "bootstrap_p_value": round(p_value, 4),
    }
