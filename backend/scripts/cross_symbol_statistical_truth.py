"""Cross-Symbol Statistical Truth: полный IS/OOS-анализ на 5 символах +
устойчивость contribution_score модулей между инструментами.

Без оптимизации: единые фиксированные пороги (min_score=30, min_families=3)
на всех символах, параметры стратегии не меняются.

Дополнительно считается ожидаемое ЧИСТО СЛУЧАЙНОЕ число «стабильных» модулей
(биномиальная вероятность попасть в топ-5 на ≥3 из 5 символов при случайном
ранжировании) — без этой поправки на множественность анализ устойчивости
самообманчив.
"""
from __future__ import annotations

import json
import math
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.statistical_truth import make_synthetic                # noqa: E402
from scripts.cross_symbol_validation import synth_funding           # noqa: E402
from app.backtest.precompute import precompute_candidates           # noqa: E402
from app.backtest.portfolio import CostModel, simulate_portfolio    # noqa: E402
from app.backtest.attribution import module_performance             # noqa: E402

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
MONTHS = 24
MIN_SCORE, MIN_FAM = 30.0, 3
IS_FRACTION = 0.7
MIN_MODULE_TRADES = 10      # минимум сделок для участия модуля в ранжировании
TOP_K = 5
STABLE_ON = 3               # «стабилен», если в топ-5 на ≥3 символах
WORSEN_THR = -0.02          # contribution_score ниже — модуль «ухудшает»


def analyze_symbol(symbol: str, seed: int) -> dict:
    t0 = time.time()
    df = make_synthetic(symbol, MONTHS, seed=seed)
    funding = synth_funding(df, seed + 1)
    cands = precompute_candidates(symbol, "1h", df, progress_every=8000,
                                  funding_df=funding)
    filtered = [c for c in cands
                if c.score >= MIN_SCORE and c.confirmations >= MIN_FAM]
    split = int(len(df) * IS_FRACTION)

    def run(lo: int, hi: int):
        seg = df.iloc[lo:hi].reset_index(drop=True)
        cs = [replace(c, bar_index=c.bar_index - lo)
              for c in filtered if lo <= c.bar_index < hi]
        return simulate_portfolio(seg, cs, CostModel())

    res_is = run(0, split)
    res_oos = run(split, len(df))

    oos_trades = [t.__dict__ for t in res_oos.trades]
    return {
        "symbol": symbol,
        "is": res_is.metrics,
        "oos": res_oos.metrics,
        "oos_trades_r": [t.pnl_r for t in res_oos.trades],
        "module_perf_oos": module_performance(oos_trades),
        "elapsed_s": round(time.time() - t0),
    }


def chance_overlap_expectation(n_eligible_per_symbol: list[int]) -> dict:
    """E[модулей в топ-5 на >=3 из 5 символов] при случайных рангах."""
    ps = [min(1.0, TOP_K / m) if m > 0 else 0.0 for m in n_eligible_per_symbol]
    p_mean = float(np.mean(ps))
    p_stable = sum(math.comb(5, k) * p_mean**k * (1 - p_mean)**(5 - k)
                   for k in range(STABLE_ON, 6))
    m_universe = max(n_eligible_per_symbol) if n_eligible_per_symbol else 0
    return {"p_top5_per_symbol": round(p_mean, 3),
            "p_stable_per_module": round(p_stable, 3),
            "expected_stable_by_chance": round(p_stable * m_universe, 2)}


def main() -> None:
    results = []
    for i, sym in enumerate(SYMBOLS):
        print(f"=== {sym} ===", flush=True)
        results.append(analyze_symbol(sym, seed=300 + i * 17))
        Path("/home/claude/csst_partial.json").write_text(
            json.dumps(results, indent=1, ensure_ascii=False))

    # ---- агрегат по объединённым OOS-сделкам
    all_r = np.array([r for x in results for r in x["oos_trades_r"]])
    gw, gl = all_r[all_r > 0].sum(), abs(all_r[all_r <= 0].sum())
    aggregate = {
        "oos_trades": int(len(all_r)),
        "winrate": round(float((all_r > 0).mean() * 100), 2),
        "profit_factor": round(float(gw / gl), 3) if gl > 0 else 999.0,
        "expectancy_r": round(float(all_r.mean()), 4),
        "is_trades": int(sum(x["is"]["n_trades"] for x in results)),
    }
    # бутстреп p-value на агрегате
    rng = np.random.default_rng(1)
    means = [rng.choice(all_r, len(all_r), replace=True).mean() for _ in range(2000)]
    aggregate["bootstrap_p_value"] = round(float(np.mean(np.array(means) <= 0)), 4)

    # ---- устойчивость contribution_score
    contrib: dict[str, dict[str, float]] = {}
    eligible_counts = []
    tops: dict[str, list[str]] = {}
    for x in results:
        elig = [p for p in x["module_perf_oos"]
                if p["total_signals"] is not None
                and p["total_signals"] >= MIN_MODULE_TRADES
                and p["contribution_score"] is not None]
        eligible_counts.append(len(elig))
        ranked = sorted(elig, key=lambda p: -p["contribution_score"])
        tops[x["symbol"]] = [p["module"] for p in ranked[:TOP_K]]
        for p in elig:
            contrib.setdefault(p["module"], {})[x["symbol"]] = p["contribution_score"]

    top_counts = {}
    for sym, names in tops.items():
        for n in names:
            top_counts[n] = top_counts.get(n, 0) + 1
    stable_top = sorted([(n, c) for n, c in top_counts.items() if c >= STABLE_ON],
                        key=lambda t: -t[1])

    worsen_counts = {}
    for mod, by_sym in contrib.items():
        cnt = sum(1 for v in by_sym.values() if v < WORSEN_THR)
        if cnt >= STABLE_ON:
            worsen_counts[mod] = {"symbols_worsening": cnt,
                                  "scores": {s: round(v, 4) for s, v in by_sym.items()}}

    # ранговая корреляция contribution_score между парами символов
    common = [m for m, d in contrib.items() if len(d) == len(SYMBOLS)]
    rho = []
    if len(common) >= 5:
        from itertools import combinations
        from scipy import stats as _st  # noqa
    corr_note = None
    try:
        from itertools import combinations
        import scipy.stats as st
        for a, b in combinations(SYMBOLS, 2):
            va = [contrib[m][a] for m in common]
            vb = [contrib[m][b] for m in common]
            r, _ = st.spearmanr(va, vb)
            rho.append(round(float(r), 3))
    except Exception as e:  # noqa: BLE001
        corr_note = f"spearman недоступен: {e}"

    out = {
        "params": {"months": MONTHS, "timeframe": "1h",
                   "min_score": MIN_SCORE, "min_families": MIN_FAM,
                   "optimization": "none"},
        "symbols": [{k: x[k] for k in ("symbol", "is", "oos")} for x in results],
        "aggregate": aggregate,
        "module_contribution_by_symbol": {m: {s: round(v, 4) for s, v in d.items()}
                                          for m, d in contrib.items()},
        "top5_by_symbol": tops,
        "stable_top_modules": stable_top,
        "consistent_worseners": worsen_counts,
        "chance_baseline": chance_overlap_expectation(eligible_counts),
        "rank_correlations": {"pairs": rho, "mean": round(float(np.mean(rho)), 3) if rho else None,
                              "note": corr_note, "n_common_modules": len(common)},
        "module_perf_full": {x["symbol"]: x["module_perf_oos"] for x in results},
    }
    Path("/home/claude/csst_results.json").write_text(
        json.dumps(out, indent=1, ensure_ascii=False))
    print("DONE")


if __name__ == "__main__":
    main()
