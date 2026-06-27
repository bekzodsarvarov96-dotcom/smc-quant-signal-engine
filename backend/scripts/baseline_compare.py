"""Baseline-замер: precompute + IS/OOS (70/30) при фиксированных порогах.

Использование (синтетика): python scripts/baseline_compare.py before|after
На реальных данных: замените make_synthetic на client.klines_history и
funding_df на client.funding_rate_history (см. backtester.run_backtest) —
сравнение before/after тогда выполняется чекаутом кода до/после фиксов.
Пороги сравнения: min_score=30, min_families=3 (как в attribution-демо).
"""
import json, sys, time
sys.path.insert(0, "/home/claude/crypto_signals/backend")
from dataclasses import replace
from scripts.statistical_truth import make_synthetic
from app.backtest.precompute import precompute_candidates
from app.backtest.portfolio import CostModel, simulate_portfolio

LABEL = sys.argv[1]              # before | after
MIN_SCORE, MIN_FAM = 30.0, 3

df = make_synthetic("BTCUSDT", 24, seed=100)

kw = {}
if LABEL == "after":
    import pandas as pd, numpy as np
    # Синтетический funding: OU-процесс вокруг +0.01%/8ч с тяжёлыми хвостами,
    # шаг 8 часов, БЕЗ корреляции с будущими доходностями (отриц. контроль)
    rng = np.random.default_rng(7)
    times = pd.date_range(df["open_time"].iloc[0].floor("8h"),
                          df["open_time"].iloc[-1], freq="8h", tz="UTC")
    x = np.zeros(len(times)); 
    for i in range(1, len(times)):
        x[i] = 0.9 * x[i-1] + rng.normal(0, 0.00012) + (rng.standard_t(3) * 0.0002 if rng.random() < 0.03 else 0)
    funding_df = pd.DataFrame({"fundingTime": times, "fundingRate": 0.0001 + x})
    kw["funding_df"] = funding_df

t0 = time.time()
cands = precompute_candidates("BTCUSDT", "1h", df, progress_every=6000, **kw)

filtered = [c for c in cands if c.score >= MIN_SCORE and c.confirmations >= MIN_FAM]
split = int(len(df) * 0.7)

def run(lo, hi):
    seg = df.iloc[lo:hi].reset_index(drop=True)
    cs = [replace(c, bar_index=c.bar_index - lo) for c in filtered if lo <= c.bar_index < hi]
    return simulate_portfolio(seg, cs, CostModel()).metrics

res = {"label": LABEL, "candidates": len(cands), "passed_filters": len(filtered),
       "is": run(0, split), "oos": run(split, len(df)),
       "elapsed_s": round(time.time() - t0)}
json.dump(res, open(f"/home/claude/baseline_{LABEL}.json", "w"), indent=1)
print(json.dumps({k: res[k] for k in ("label","candidates","passed_filters")},))
