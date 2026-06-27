"""Cross-Symbol Statistical Truth: полный IS/OOS-анализ на 5 символах
без какой-либо оптимизации (фиксированные пороги min_score=30, min_families=3,
сплит IS/OOS = 70/30, издержки включены).

По каждому символу: IS/OOS trades, Winrate, PF, Expectancy R, Sharpe,
Max Drawdown, Total Return + module contribution_score по OOS-сделкам.
Затем: агрегат по объединённым OOS-сделкам и анализ устойчивости модулей:
  • топ-5 по contribution_score на каждом символе → модули в топ-5 на ≥3;
  • модули с отрицательным вкладом на ≥3 символах;
  • нулевой ориентир: сколько таких совпадений даёт чистый шум.
"""
from __future__ import annotations

import argparse
import asyncio
import argparse
import asyncio
import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.statistical_truth import make_synthetic                 # noqa: E402
from scripts.cross_symbol_validation import synth_funding            # noqa: E402
from app.backtest.precompute import precompute_candidates            # noqa: E402
from app.backtest.portfolio import CostModel, simulate_portfolio     # noqa: E402
from app.backtest.attribution import module_performance              # noqa: E402

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
MONTHS = 24
MIN_SCORE, MIN_FAM = 30.0, 3
IS_FRACTION = 0.7
MIN_N = 10        # минимум сделок для статистики модуля


REAL_DATA = False


def load_data(symbol: str, seed: int):
    """Источник данных: синтетика (по умолчанию) или Binance (--real)."""
    if not REAL_DATA:
        df = make_synthetic(symbol, MONTHS, seed=seed)
        return df, synth_funding(df, seed + 1)
    from datetime import datetime, timedelta, timezone
    from app.binance.client import get_client
    async def fetch():
        client = get_client()
        start = datetime.now(timezone.utc) - timedelta(days=MONTHS * 30)
        df = await client.klines_history(symbol, "1h", start)
        f = await client.funding_rate_history(symbol, start)
        await client.close()
        return df, f
    return asyncio.run(fetch())


def analyze(symbol: str, seed: int) -> dict:
    t0 = time.time()
    df, funding = load_data(symbol, seed)
    if funding is not None and getattr(funding, "empty", True) is False:
        cost_series = funding.set_index("fundingTime")["fundingRate"]
    else:
        funding, cost_series = None, None
    cands = precompute_candidates(symbol, "1h", df, progress_every=8000,
                                  funding_df=funding)
    filtered = [c for c in cands
                if c.score >= MIN_SCORE and c.confirmations >= MIN_FAM]
    split = int(len(df) * IS_FRACTION)

    def run(lo: int, hi: int):
        seg = df.iloc[lo:hi].reset_index(drop=True)
        cs = [replace(c, bar_index=c.bar_index - lo)
              for c in filtered if lo <= c.bar_index < hi]
        return simulate_portfolio(seg, cs, CostModel(funding_series=cost_series))

    is_res = run(0, split)
    oos_res = run(split, len(df))
    perf = module_performance([t.__dict__ for t in oos_res.trades])

    print(f"{symbol}: done in {time.time()-t0:.0f}s "
          f"(IS n={is_res.metrics['n_trades']}, OOS n={oos_res.metrics['n_trades']})",
          flush=True)
    return {
        "symbol": symbol,
        "is": is_res.metrics,
        "oos": oos_res.metrics,
        "oos_trades_r": [t.pnl_r for t in oos_res.trades],
        "module_perf_oos": perf,
    }


def main() -> None:
    global REAL_DATA, MONTHS
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true",
                    help="реальные данные Binance (fapi.binance.com)")
    ap.add_argument("--months", type=int, default=MONTHS)
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    ap.add_argument("--out", default="CROSS_SYMBOL_TRUTH_REPORT.md")
    args = ap.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    REAL_DATA, MONTHS = args.real, args.months

    results = []
    for i, sym in enumerate(symbols):
        results.append(analyze(sym, seed=300 + i * 17))
        Path("cst_partial.json").write_text(json.dumps(results, indent=1))

    # ---------- агрегат ----------
    all_r = np.array([r for x in results for r in x["oos_trades_r"]])
    gw, gl = all_r[all_r > 0].sum(), abs(all_r[all_r <= 0].sum())
    boot = np.random.default_rng(1).choice(all_r, size=(2000, len(all_r)), replace=True).mean(axis=1)
    aggregate = {
        "oos_trades": int(len(all_r)),
        "winrate": round(float((all_r > 0).mean() * 100), 2),
        "profit_factor": round(float(gw / gl), 3) if gl else 0.0,
        "expectancy_r": round(float(all_r.mean()), 4),
        "bootstrap_p_value_exp_gt_0": round(float((boot <= 0).mean()), 4),
        "per_symbol_exp_mean": round(float(np.mean([x["oos"]["expectancy_r"] for x in results])), 4),
        "per_symbol_exp_std": round(float(np.std([x["oos"]["expectancy_r"] for x in results], ddof=1)), 4) if len(results) > 1 else 0.0,
        "symbols_positive": int(sum(1 for x in results if x["oos"]["expectancy_r"] > 0)),
    }

    # ---------- устойчивость contribution_score ----------
    contrib: dict[str, dict[str, float]] = defaultdict(dict)   # module -> {symbol: cs}
    eligible_per_symbol: dict[str, list[tuple[str, float]]] = {}
    for x in results:
        rows = [(p["module"], p["contribution_score"]) for p in x["module_perf_oos"]
                if p["contribution_score"] is not None and p["total_signals"] >= MIN_N]
        eligible_per_symbol[x["symbol"]] = rows
        for m, cs in rows:
            contrib[m][x["symbol"]] = cs

    top5_counts: Counter = Counter()
    for sym, rows in eligible_per_symbol.items():
        for m, _ in sorted(rows, key=lambda r: -r[1])[:5]:
            top5_counts[m] += 1
    top5_3plus = sorted([(m, c) for m, c in top5_counts.items() if c >= 3],
                        key=lambda t: -t[1])

    neg_counts: Counter = Counter()
    for sym, rows in eligible_per_symbol.items():
        for m, cs in rows:
            if cs < -0.02:
                neg_counts[m] += 1
    neg_3plus = sorted([(m, c) for m, c in neg_counts.items() if c >= 3],
                       key=lambda t: -t[1])

    stability = []
    for m, d in contrib.items():
        if len(d) >= 3:
            v = np.array(list(d.values()))
            stability.append({
                "module": m, "symbols": len(d),
                "mean_cs": round(float(v.mean()), 4),
                "std_cs": round(float(v.std(ddof=1)), 4),
                "n_positive": int((v > 0).sum()),
                "n_negative": int((v < 0).sum()),
                "values": {k: round(val, 4) for k, val in d.items()},
            })
    stability.sort(key=lambda s: -s["mean_cs"])

    # Нулевой ориентир совпадений топ-5 (перестановочный тест по факт. числу
    # допущенных модулей на символ)
    rng = np.random.default_rng(2)
    sizes = [len(rows) for rows in eligible_per_symbol.values()]
    null_counts = []
    for _ in range(2000):
        c = Counter()
        for n_elig in sizes:
            ids = rng.choice(n_elig, size=min(5, n_elig), replace=False)
            for i in ids:
                c[i] += 1
        null_counts.append(sum(1 for v in c.values() if v >= 3))
    null_top5 = {"mean": round(float(np.mean(null_counts)), 2),
                 "p95": int(np.percentile(null_counts, 95))}

    out = {"symbols": results, "aggregate": aggregate,
           "module_stability": stability,
           "top5_on_3plus": top5_3plus, "negative_on_3plus": neg_3plus,
           "null_expectation_top5_3plus": null_top5,
           "params": {"min_score": MIN_SCORE, "min_families": MIN_FAM,
                      "is_fraction": IS_FRACTION, "months": MONTHS,
                      "optimization": "NONE (фиксированные параметры)"}}
    out["params"]["data_source"] = "binance_real" if args.real else "synthetic_control"
    Path("cst_results.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    Path(args.out).write_text(render_report(out), encoding="utf-8")
    print(f"DONE → {args.out} + cst_results.json")


def render_report(d: dict) -> str:
    real = d["params"].get("data_source") == "binance_real"
    L = ["# Cross-Symbol Statistical Truth — отчёт",
         f"**Источник данных:** {'РЕАЛЬНЫЕ ДАННЫЕ Binance Futures' if real else 'синтетический контроль (без edge)'} • "
         f"{d['params']['months']} мес • 1h • IS/OOS 70/30 • пороги фиксированы "
         f"(score≥{d['params']['min_score']:.0f}, семейств≥{d['params']['min_families']}) • оптимизации не было", ""]
    L += ["## Результаты по символам (OOS)",
          "| Символ | IS n | OOS n | WR | PF | Exp R | Sharpe | MaxDD | TotRet |",
          "|---|---|---|---|---|---|---|---|---|"]
    for x in d["symbols"]:
        o, i = x["oos"], x["is"]
        L.append(f"| {x['symbol']} | {i['n_trades']} | {o['n_trades']} | {o['winrate']:.1f}% "
                 f"| {o['profit_factor']:.3f} | {o['expectancy_r']:+.4f} | {o['sharpe']:+.2f} "
                 f"| {o['max_drawdown_pct']:.1f}% | {o['total_return_pct']:+.1f}% |")
    a = d["aggregate"]
    L += ["", "## Агрегат (объединённые OOS-сделки)",
          f"- Сделок: **{a['oos_trades']}** • WR **{a['winrate']}%** • PF **{a['profit_factor']}** "
          f"• Expectancy **{a['expectancy_r']:+.4f}R**",
          f"- Бутстреп p-value (H0: E[R]≤0): **{a['bootstrap_p_value_exp_gt_0']}**",
          f"- Символов с exp>0: **{a['symbols_positive']} из {len(d['symbols'])}** "
          f"(межсимвольный std {a['per_symbol_exp_std']:.4f}R)", ""]
    L += ["## Устойчивость модулей",
          f"- Топ-5 на ≥3 символах: **{', '.join(m for m, _ in d['top5_on_3plus']) or '—'}**",
          f"- Нулевой ориентир (шум): {d['null_expectation_top5_3plus']['mean']} модулей, "
          f"p95={d['null_expectation_top5_3plus']['p95']}",
          f"- Стабильно ухудшают на ≥3: **{', '.join(m for m, _ in d['negative_on_3plus']) or '—'}**", "",
          "| Модуль | mean CS | std | +/− |", "|---|---|---|---|"]
    for s in d["module_stability"]:
        L.append(f"| {s['module']} | {s['mean_cs']:+.4f} | {s['std_cs']:.4f} | "
                 f"{s['n_positive']}/{s['n_negative']} |")
    edge = (a["expectancy_r"] > 0 and a["profit_factor"] > 1.10
            and a["bootstrap_p_value_exp_gt_0"] < 0.05
            and a["symbols_positive"] >= 4 and a["oos_trades"] >= 100)
    L += ["", "## Вердикт",
          f"**{'✅ Признаки рыночного edge: критерии выполнены' if edge else '❌ Edge НЕ доказан'}** "
          f"(критерии: exp>0, PF>1.10, p<0.05, ≥4/5 символов в плюсе, ≥100 OOS-сделок).",
          "" if real else "_Синтетический контроль: корректный результат — отсутствие edge._",
          "_Устойчивость модулей сравнивайте с контрольным прогоном (CROSS_SYMBOL_TRUTH.md): "
          "модуль ценен, если стабилен на реале и НЕ был стабильно «полезен» на шуме._"]
    return "\n".join(L)


if __name__ == "__main__":
    main()
