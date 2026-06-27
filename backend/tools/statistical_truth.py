"""Statistical Truth v1 — оркестратор полного статистического отчёта.

Запуск на РЕАЛЬНЫХ данных Binance (нужен доступ к fapi.binance.com):
    cd backend && python -m tools.statistical_truth --symbols BTCUSDT ETHUSDT SOLUSDT \
        --timeframe 1h --months 24 --out report.md

Запуск ДЕМО на синтетических данных (без сети):
    python -m tools.statistical_truth --demo --out report_demo.md
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app.backtest.stat_truth import (
    collect_candidates, is_oos_report, walk_forward, verdict, WARMUP,
)


# ---------------------------------------------------------------- данные

async def load_real(symbol: str, timeframe: str, months: int):
    from app.binance.client import get_client
    client = get_client()
    start = datetime.now(timezone.utc) - timedelta(days=months * 30)
    df = await client.klines_history(symbol, timeframe, start)
    f = await client.funding_history_paginated(symbol, start) \
        if hasattr(client, "funding_history_paginated") else None
    funding = f["fundingRate"].reset_index(drop=True) if f is not None and not f.empty else None
    return df, funding


def make_synthetic(symbol: str, months: int, timeframe: str = "1h",
                   seed: int = 0) -> tuple[pd.DataFrame, pd.Series]:
    """Реалистичный синтетический рынок: режимы (тренд/флэт/крах),
    кластеризация волатильности, объёмная сезонность, funding-серия."""
    rng = np.random.default_rng(seed)
    bars_per_day = {"15m": 96, "1h": 24, "4h": 6}[timeframe]
    n = months * 30 * bars_per_day

    drift = np.zeros(n)
    vol = np.zeros(n)
    regime, i = 0, 0
    while i < n:
        length = int(rng.integers(bars_per_day * 5, bars_per_day * 40))
        regime = rng.choice([0, 1, 2], p=[0.45, 0.45, 0.10])  # up / range / down-crash
        d = {0: 0.0006, 1: 0.0, 2: -0.0015}[regime]
        v = {0: 0.006, 1: 0.004, 2: 0.014}[regime]
        drift[i:i + length] = d
        vol[i:i + length] = v
        i += length

    # GARCH-подобная кластеризация волатильности
    eps = rng.standard_normal(n)
    sigma = vol * (1 + 0.5 * np.abs(np.convolve(eps, np.ones(12) / 12, "same")))
    log_ret = drift + sigma * eps
    close = 30_000 * np.exp(np.cumsum(log_ret)) if "BTC" in symbol else \
            2_000 * np.exp(np.cumsum(log_ret)) if "ETH" in symbol else \
            100 * np.exp(np.cumsum(log_ret))

    op = close * (1 + rng.normal(0, 0.0008, n))
    hi = np.maximum(op, close) * (1 + np.abs(rng.normal(0, 0.0025, n)))
    lo = np.minimum(op, close) * (1 - np.abs(rng.normal(0, 0.0025, n)))
    hour = np.arange(n) % 24
    season = 1 + 0.4 * np.sin((hour - 14) / 24 * 2 * np.pi)   # пик на NY-сессии
    volu = np.abs(rng.normal(1000, 300, n)) * season * (sigma / vol.mean())

    df = pd.DataFrame({
        "open_time": pd.date_range("2024-06-01", periods=n, freq="1h", tz="UTC"),
        "open": op, "high": hi, "low": lo, "close": close,
        "volume": volu, "taker_buy_base": volu * np.clip(
            0.5 + 8 * log_ret + rng.normal(0, 0.05, n), 0.2, 0.8),
    })
    # Funding раз в 8 баров (8ч на 1h): следует за импульсом цены
    momentum = pd.Series(log_ret).rolling(24, min_periods=1).mean().values[::8][: n // 8]
    funding = pd.Series(np.clip(
        0.0001 + 1.5 * momentum + rng.normal(0, 0.00005, len(momentum)),
        -0.002, 0.002))
    return df, funding


# ---------------------------------------------------------------- отчёт

def fmt_metrics(m: dict) -> str:
    return (f"| Number of Trades | {m['n_trades']} |\n"
            f"| Win Rate | {m['winrate']:.1f}% |\n"
            f"| Profit Factor | {m['profit_factor']} |\n"
            f"| Expectancy | {m['expectancy']:+.4f} R/сделка |\n"
            f"| Average RR (победители) | {m['avg_rr']} |\n"
            f"| Sharpe Ratio | {m['sharpe']} |\n"
            f"| Max Drawdown | {m['max_drawdown_r']} R |\n"
            f"| Total | {m['total_r']:+.1f} R |\n"
            f"| Средние издержки/сделка | {m['avg_costs_r']:.4f} R |\n"
            f"| Макс. одновременных позиций | {m['max_concurrent']} |")


def run_symbol(symbol: str, df: pd.DataFrame, funding, timeframe: str) -> dict:
    print(f"\n=== {symbol}: {len(df)} баров ===")
    print("  PASS 1: сбор кандидатов…")
    cands = collect_candidates(df, symbol, timeframe)
    scores = np.array([c.score for c in cands])
    print(f"  Кандидатов: {len(cands)}; score p50={np.median(scores):.1f} "
          f"p90={np.percentile(scores, 90):.1f} max={scores.max():.1f}")

    print("  IS/OOS отчёт…")
    rep = is_oos_report(df, cands, funding, timeframe)
    print("  Walk-Forward…")
    wfa = walk_forward(df, cands, funding, timeframe)
    v, notes = verdict(rep["out_of_sample"])
    return {"symbol": symbol, "candidates": len(cands),
            "score_dist": {
                "p10": round(float(np.percentile(scores, 10)), 1),
                "p50": round(float(np.median(scores)), 1),
                "p90": round(float(np.percentile(scores, 90)), 1),
                "p98": round(float(np.percentile(scores, 98)), 1),
                "max": round(float(scores.max()), 1)},
            "report": rep, "walk_forward": wfa,
            "verdict": v, "verdict_notes": notes}


def render_md(results: list[dict], demo: bool, months: int, timeframe: str) -> str:
    lines = ["# Statistical Truth v1 — отчёт\n"]
    if demo:
        lines.append("> ⚠️ **ДЕМО-РЕЖИМ: синтетические данные.** Цифры демонстрируют "
                     "работу контура, а НЕ реальное преимущество на рынке. Для реальных "
                     "данных запустите скрипт с доступом к fapi.binance.com.\n")
    lines.append(f"Период: {months} мес • ТФ: {timeframe} • Издержки: комиссия 0.05%/сторона, "
                 "слиппедж 2 б.п./сторона, funding-платежи каждые 8ч • "
                 "Метод: IS 75% (калибровка порогов) / OOS 25%, anchored WFA 6 фолдов, "
                 "Monte Carlo 1000 bootstrap.\n")
    for r in results:
        rep = r["report"]
        lines.append(f"\n## {r['symbol']}\n")
        sd = r["score_dist"]
        lines.append(f"Кандидатов: {r['candidates']} • score: p50={sd['p50']} "
                     f"p90={sd['p90']} p98={sd['p98']} max={sd['max']}")
        cal = rep["calibration"]
        lines.append(f"Калибровка (IS): пороги A+={cal['grade_thresholds']['A+']} "
                     f"A={cal['grade_thresholds']['A']} B={cal['grade_thresholds']['B']}, "
                     f"min_score={cal['min_score']}, семейств ≥{cal['min_families']}\n")
        for label, title in (("in_sample", "In-Sample"), ("out_of_sample", "Out-Of-Sample")):
            m = rep[label]
            lines.append(f"\n### {title}\n\n| Метрика | Значение |\n|---|---|")
            lines.append(fmt_metrics(m))
            mc = m.get("monte_carlo", {})
            if "error" not in mc:
                lines.append(f"\nMonte Carlo: P(прибыль)={mc['p_profit']:.0%}, "
                             f"EV CI95=[{mc['expectancy_ci95'][0]}; {mc['expectancy_ci95'][1]}] R, "
                             f"MaxDD p95={mc['max_dd_p95']} R")
        lines.append("\n### Walk-Forward (OOS-фолды)\n")
        lines.append("| Фолд | min_score | Сделок | WR % | EV (R) | PF | Total R |")
        lines.append("|---|---|---|---|---|---|---|")
        for f in r["walk_forward"]:
            lines.append(f"| {f['fold']} | {f['min_score']} | {f['n_trades']} | "
                         f"{f['winrate']} | {f['expectancy']:+.3f} | "
                         f"{f['profit_factor']} | {f['total_r']:+.1f} |")
        lines.append(f"\n**Вердикт {r['symbol']}: {r['verdict']}**")
        for n in r["verdict_notes"]:
            lines.append(f"- {n}")
    # Общий вердикт
    oos_evs = [r["report"]["out_of_sample"]["expectancy"] for r in results]
    confirmed = sum(1 for r in results if r["verdict"].startswith("ПРЕИМУЩЕСТВО ПОДТВЕРЖДЕНО"))
    lines.append("\n---\n## Итоговый вердикт по системе\n")
    if confirmed == len(results):
        lines.append("**Статистическое преимущество подтверждено на всех символах "
                     "(в пределах исследованного периода).**")
    elif confirmed > 0:
        lines.append(f"**Преимущество подтверждено на {confirmed}/{len(results)} символах — "
                     "система нестабильна между инструментами; торговать только "
                     "подтверждённые, продолжить исследование.**")
    else:
        lines.append("**Статистическое преимущество НЕ подтверждено: по OOS-данным "
                     "система не зарабатывает после издержек. Торговать реальными "
                     "деньгами нельзя; требуется доработка логики, а не порогов.**")
    lines.append(f"\nOOS expectancy по символам: {oos_evs} R/сделка")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--months", type=int, default=24)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--out", default="statistical_truth_report.md")
    args = ap.parse_args()

    results = []
    for idx, sym in enumerate(args.symbols):
        if args.demo:
            df, funding = make_synthetic(sym, args.months, args.timeframe, seed=idx * 7 + 1)
        else:
            df, funding = asyncio.run(load_real(sym, args.timeframe, args.months))
        results.append(run_symbol(sym, df, funding, args.timeframe))

    md = render_md(results, args.demo, args.months, args.timeframe)
    with open(args.out, "w") as f:
        f.write(md)
    json_out = args.out.replace(".md", ".json")
    with open(json_out, "w") as f:
        json.dump([{k: v for k, v in r.items() if k != "report"} | {
            "calibration": r["report"]["calibration"],
            "in_sample": {k: v for k, v in r["report"]["in_sample"].items()
                          if not k.startswith("_")},
            "out_of_sample": {k: v for k, v in r["report"]["out_of_sample"].items()
                              if not k.startswith("_")},
        } for r in results], f, ensure_ascii=False, indent=2, default=str)
    print(f"\nОтчёт: {args.out} (+ {json_out})")


if __name__ == "__main__":
    main()
