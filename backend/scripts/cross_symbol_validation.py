"""Cross-Symbol Validation: одинаковый OOS-анализ на N символах.

Для каждого символа: precompute (с funding в контексте, без look-ahead) →
фильтры min_score=30 / min_families=3 → портфельная симуляция с издержками →
OOS-метрики (последние 30%). Затем: пер-символьная таблица, агрегат по
объединённым OOS-сделкам и дисперсия метрик между символами.

Синтетический режим — отрицательный контроль: серии независимы и не содержат
edge; межсимвольная дисперсия здесь = эталон чистого шума для сравнения
с реальными данными. Реальный режим — см. baseline_compare.py / backtester.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.statistical_truth import make_synthetic                # noqa: E402
from app.backtest.precompute import precompute_candidates           # noqa: E402
from app.backtest.portfolio import CostModel, simulate_portfolio    # noqa: E402

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
MONTHS = 24
MIN_SCORE, MIN_FAM = 30.0, 3
IS_FRACTION = 0.7


def synth_funding(df: pd.DataFrame, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    times = pd.date_range(df["open_time"].iloc[0].floor("8h"),
                          df["open_time"].iloc[-1], freq="8h", tz="UTC")
    x = np.zeros(len(times))
    for i in range(1, len(times)):
        x[i] = 0.9 * x[i - 1] + rng.normal(0, 0.00012) + \
               (rng.standard_t(3) * 0.0002 if rng.random() < 0.03 else 0)
    return pd.DataFrame({"fundingTime": times, "fundingRate": 0.0001 + x})


def analyze_symbol(symbol: str, seed: int) -> dict:
    t0 = time.time()
    df = make_synthetic(symbol, MONTHS, seed=seed)
    funding = synth_funding(df, seed + 1)
    cands = precompute_candidates(symbol, "1h", df, progress_every=8000,
                                  funding_df=funding)
    filtered = [c for c in cands
                if c.score >= MIN_SCORE and c.confirmations >= MIN_FAM]
    split = int(len(df) * IS_FRACTION)
    seg = df.iloc[split:].reset_index(drop=True)
    cs = [replace(c, bar_index=c.bar_index - split)
          for c in filtered if c.bar_index >= split]
    res = simulate_portfolio(seg, cs, CostModel())
    m = res.metrics
    return {
        "symbol": symbol,
        "oos": m,
        "oos_trades_r": [t.pnl_r for t in res.trades],
        "candidates": len(cands),
        "passed_filters": len(filtered),
        "elapsed_s": round(time.time() - t0),
    }


def pf(rs: np.ndarray) -> float:
    gw, gl = rs[rs > 0].sum(), abs(rs[rs <= 0].sum())
    return round(float(gw / gl), 3) if gl > 0 else 999.0


def main() -> None:
    results = []
    for i, sym in enumerate(SYMBOLS):
        print(f"=== {sym} ===", flush=True)
        results.append(analyze_symbol(sym, seed=300 + i * 17))
        Path("/home/claude/cross_symbol_partial.json").write_text(
            json.dumps(results, indent=1))

    # Агрегат по объединённым OOS-сделкам
    all_r = np.array([r for x in results for r in x["oos_trades_r"]])
    agg = {
        "n_trades": int(len(all_r)),
        "winrate": round(float((all_r > 0).mean() * 100), 2) if len(all_r) else 0,
        "profit_factor": pf(all_r) if len(all_r) else 0,
        "expectancy_r": round(float(all_r.mean()), 4) if len(all_r) else 0,
    }

    # Дисперсия между символами
    def col(key): return np.array([x["oos"][key] for x in results])
    disp = {}
    for key in ("profit_factor", "expectancy_r", "winrate"):
        v = col(key)
        disp[key] = {
            "mean": round(float(v.mean()), 4),
            "std": round(float(v.std(ddof=1)), 4),
            "min": round(float(v.min()), 4),
            "max": round(float(v.max()), 4),
            "range": round(float(v.max() - v.min()), 4),
        }
    n_profitable = int(sum(1 for x in results if x["oos"]["expectancy_r"] > 0))

    out = {"symbols": results, "aggregate": agg, "dispersion": disp,
           "profitable_symbols": n_profitable,
           "params": {"months": MONTHS, "min_score": MIN_SCORE,
                      "min_families": MIN_FAM, "oos_fraction": 1 - IS_FRACTION}}
    Path("/home/claude/cross_symbol_results.json").write_text(
        json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps({"aggregate": agg, "dispersion": disp}, indent=1))


if __name__ == "__main__":
    main()
