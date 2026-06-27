"""Statistical Truth v1 — полный статистический аудит системы.

Пайплайн на символ:
  1. Данные: 24 мес 1h-свечей (Binance) ИЛИ синтетика (--synthetic — отриц. контроль).
  2. Прекомпьют кандидатов (пороги выключены) с capped-окном.
  3. Калибровка: распределение score → перцентильные границы A+/A/B/C
     (по первым 70% истории, чтобы не подглядывать в OOS) → calibration.json.
  4. Walk-Forward (6 фолдов): grid-search порогов на train, тест на следующем
     сегменте → честные IS/OOS метрики (комиссии, funding, slippage, портфель).
  5. Monte Carlo (1000 путей) по OOS-сделкам + бутстреп p-value для E[R]>0.
  6. Markdown-отчёт с вердиктом.

Запуск на реальных данных (с машины с доступом к Binance):
    cd backend && python -m scripts.statistical_truth --symbols BTCUSDT,ETHUSDT,SOLUSDT
Синтетический отрицательный контроль:
    python -m scripts.statistical_truth --synthetic
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backtest.precompute import precompute_candidates           # noqa: E402
from app.backtest.portfolio import CostModel                        # noqa: E402
from app.backtest.walk_forward import walk_forward                  # noqa: E402
from app.backtest.monte_carlo import monte_carlo                    # noqa: E402
from app.engine import scoring                                      # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("statistical_truth")

CALIB_PATH = Path(__file__).resolve().parents[1] / "app" / "engine" / "calibration.json"

VERDICT_RULES = """Критерии «преимущество доказано» (ВСЕ должны выполняться на OOS):
  1. сделок OOS >= 100;
  2. expectancy (R) > 0 после всех издержек;
  3. profit factor > 1.10;
  4. bootstrap p-value (H0: E[R]<=0) < 0.05;
  5. Monte-Carlo prob_profit >= 0.90."""


# ---------------------------------------------------------------- данные

def make_synthetic(symbol: str, months: int, seed: int) -> pd.DataFrame:
    """Случайное блуждание с реалистичной часовой волатильностью и
    кластеризацией волатильности. НЕ содержит эксплуатируемой структуры —
    отрицательный контроль: честная система обязана показать отсутствие edge."""
    rng = np.random.default_rng(seed)
    n = months * 30 * 24
    vol = np.empty(n)
    vol[0] = 0.008
    for i in range(1, n):                          # GARCH-подобная кластеризация
        vol[i] = np.clip(0.9 * vol[i - 1] + 0.1 * abs(rng.normal(0, 0.01)), 0.003, 0.05)
    rets = rng.normal(0, 1, n) * vol
    price0 = {"BTCUSDT": 45_000, "ETHUSDT": 2_500, "SOLUSDT": 100}.get(symbol, 100)
    close = price0 * np.exp(np.cumsum(rets))
    op = np.roll(close, 1); op[0] = close[0]
    intrabar = np.abs(rng.normal(0, 0.4, n)) * vol * close
    high = np.maximum(op, close) + intrabar
    low = np.minimum(op, close) - intrabar
    base_vol = np.abs(rng.normal(1.0, 0.3, n)) * (1 + 8 * vol / vol.mean() * 0.1)
    start = datetime.now(timezone.utc) - timedelta(hours=n)
    times = pd.date_range(start, periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({
        "open_time": times, "open": op, "high": high, "low": low, "close": close,
        "volume": base_vol * 1000,
        "taker_buy_base": base_vol * 1000 * rng.uniform(0.35, 0.65, n),
    })


async def fetch_real(symbol: str, months: int) -> tuple[pd.DataFrame, pd.Series]:
    from app.binance.client import get_client
    client = get_client()
    start = datetime.now(timezone.utc) - timedelta(days=months * 30)
    df = await client.klines_history(symbol, "1h", start)
    f = await client.funding_rate_history(symbol, start)   # пагинация: весь период
    funding = f.set_index("fundingTime")["fundingRate"] if not f.empty else pd.Series(dtype=float)
    return df, funding


# ---------------------------------------------------------------- калибровка

def calibrate_grades(candidates, is_fraction: float = 0.7, max_bar: int | None = None) -> dict:
    """Перцентили score по In-Sample части кандидатов → границы грейдов."""
    if max_bar is None and candidates:
        max_bar = int(max(c.bar_index for c in candidates) * is_fraction)
    scores = np.array([c.score for c in candidates if c.bar_index <= (max_bar or 0)])
    if len(scores) < 100:
        scores = np.array([c.score for c in candidates]) if candidates else np.array([0.0])
    p = {f"p{q}": round(float(np.percentile(scores, q)), 1) for q in (50, 75, 90, 97)}
    cutoffs = {"A+": p["p97"], "A": p["p90"], "B": p["p75"], "C": p["p50"]}
    return {"percentiles": p, "cutoffs": cutoffs, "n": len(scores),
            "hist": np.histogram(scores, bins=10, range=(0, 100))[0].tolist()}


def save_calibration(calib: dict, source: str) -> None:
    CALIB_PATH.write_text(json.dumps({
        "source": source,
        "candidates": calib["n"],
        "percentiles": calib["percentiles"],
        "cutoffs": calib["cutoffs"],
    }, indent=2, ensure_ascii=False))
    scoring.GRADE_CUTOFFS = calib["cutoffs"]


def regrade(candidates, cutoffs) -> None:
    for c in candidates:
        htf_ok = True  # htf уже учтён жёстким фильтром при прекомпьюте
        c.grade = scoring.grade_of(c.score, c.confirmations, htf_ok, cutoffs)


# ---------------------------------------------------------------- отчёт

def ascii_hist(hist: list[int]) -> str:
    mx = max(hist) or 1
    lines = []
    for i, h in enumerate(hist):
        bar = "█" * max(1, int(40 * h / mx)) if h else ""
        lines.append(f"  {i*10:>3}–{i*10+10:<3} | {bar} {h}")
    return "\n".join(lines)


def fmt_metrics(m: dict) -> str:
    pf = m["profit_factor"]
    pf_s = f"{pf:.3f}" if pf != float("inf") else "inf"
    return (f"| Сделок | {m['n_trades']} |\n"
            f"| Win Rate | {m['winrate']:.2f}% |\n"
            f"| Profit Factor | {pf_s} |\n"
            f"| Expectancy | {m['expectancy_r']:+.4f} R |\n"
            f"| Average RR (победители) | {m['avg_rr']:.3f} |\n"
            f"| Sharpe (дневн., годовой) | {m['sharpe']:.3f} |\n"
            f"| Max Drawdown (equity) | {m['max_drawdown_pct']:.2f}% |\n"
            f"| Итоговая доходность | {m['total_return_pct']:+.2f}% |\n"
            f"| Комиссии / Funding | ${m['total_fees']:.2f} / ${m['total_funding']:.2f} |")


def verdict_for(oos: dict, mc: dict) -> tuple[bool, list[str]]:
    checks = []
    ok = True
    def chk(cond, label):
        nonlocal ok
        checks.append(("✅" if cond else "❌") + " " + label)
        ok = ok and cond
    chk(oos["n_trades"] >= 100, f"сделок OOS >= 100 (факт: {oos['n_trades']})")
    chk(oos["expectancy_r"] > 0, f"expectancy > 0 (факт: {oos['expectancy_r']:+.4f}R)")
    pf = oos["profit_factor"]
    chk(pf > 1.10, f"profit factor > 1.10 (факт: {pf if pf != float('inf') else 'inf'})")
    if mc.get("valid"):
        chk(mc["bootstrap_p_value"] < 0.05, f"bootstrap p < 0.05 (факт: {mc['bootstrap_p_value']})")
        chk(mc["prob_profit"] >= 0.90, f"MC prob_profit >= 0.90 (факт: {mc['prob_profit']})")
    else:
        chk(False, f"Monte Carlo невалиден: {mc.get('reason')}")
    return ok, checks


# ---------------------------------------------------------------- main

async def run(symbols: list[str], months: int, synthetic: bool, out: Path) -> None:
    t0 = time.time()
    mode = "СИНТЕТИЧЕСКИЕ ДАННЫЕ (отрицательный контроль)" if synthetic else "реальные данные Binance"
    report = [
        "# Statistical Truth v1 — отчёт",
        f"**Режим:** {mode} • **Период:** {months} мес • **ТФ:** 1h • "
        f"**Издержки:** taker 0.05%/сторона, slippage 2 б.п./сторона, funding учтён",
        "",
    ]
    if synthetic:
        report += [
            "> ⚠️ Это **отрицательный контроль** на случайном блуждании без",
            "> эксплуатируемой структуры. Назначение — проверить честность пайплайна:",
            "> корректная система ОБЯЗАНА показать здесь «преимущества нет».",
            "> Вердикт о реальном edge возможен только на реальных данных Binance.",
            "",
        ]

    all_verdicts = []
    calib_global = None

    for si, symbol in enumerate(symbols):
        logger.info("=== %s ===", symbol)
        if synthetic:
            df = make_synthetic(symbol, months, seed=100 + si)
            funding = pd.Series(dtype=float)
        else:
            df, funding = await fetch_real(symbol, months)
        logger.info("%s: %d свечей", symbol, len(df))

        f_df = None
        if not synthetic and funding is not None and not funding.empty:
            f_df = (funding.rename("fundingRate").reset_index()
                    .rename(columns={funding.index.name or "index": "fundingTime"}))
        cands = precompute_candidates(symbol, "1h", df, enforce_htf=True,
                                      funding_df=f_df)
        logger.info("%s: %d кандидатов", symbol, len(cands))

        calib = calibrate_grades(cands)
        if calib_global is None:
            calib_global = calib
            save_calibration(calib, f"{'synthetic' if synthetic else 'binance'}:{symbol}:{months}m")
        regrade(cands, calib["cutoffs"])

        costs = CostModel(funding_series=funding if not funding.empty else None)
        wfa = walk_forward(df, cands, costs, n_folds=6)
        mc = monte_carlo([t.pnl_r for t in wfa.oos_trades])
        ok, checks = verdict_for(wfa.oos_metrics, mc)
        all_verdicts.append(ok)

        grades = {}
        for c in cands:
            grades[c.grade] = grades.get(c.grade, 0) + 1

        report += [
            f"## {symbol}",
            f"Свечей: {len(df)} • Кандидатов (до порогов): {len(cands)} • "
            f"Грейды после калибровки: {dict(sorted(grades.items()))}",
            "",
            "### Распределение score кандидатов",
            "```",
            ascii_hist(calib["hist"]),
            "```",
            f"Перцентили: {calib['percentiles']} → границы: "
            f"A+ ≥ {calib['cutoffs']['A+']}, A ≥ {calib['cutoffs']['A']}, "
            f"B ≥ {calib['cutoffs']['B']}, C ≥ {calib['cutoffs']['C']}",
            "",
            "### Walk-Forward (6 фолдов, grid: score × семейства)",
            "| Fold | Параметры | IS сделок | IS exp (R) | OOS сделок | OOS exp (R) |",
            "|---|---|---|---|---|---|",
        ]
        for f in wfa.folds:
            report.append(
                f"| {f['fold']} | score≥{f['chosen_min_score']:.0f}, fam≥{f['chosen_min_families']} "
                f"| {f['is_trades']} | {f['is_expectancy']:+.3f} "
                f"| {f['oos_trades']} | {f['oos_expectancy']:+.3f} |")
        report += [
            "",
            "### In-Sample (агрегат train-сегментов)",
            "| Метрика | Значение |", "|---|---|", fmt_metrics(wfa.is_metrics),
            "",
            "### Out-of-Sample (агрегат test-сегментов) — главная таблица",
            "| Метрика | Значение |", "|---|---|", fmt_metrics(wfa.oos_metrics),
            "",
            "### Monte Carlo (1000 путей по OOS-сделкам)",
        ]
        if mc.get("valid"):
            report += [
                f"- Итоговый R: p5 = {mc['total_r_p5']}, медиана = {mc['total_r_p50']}, "
                f"p95 = {mc['total_r_p95']}",
                f"- Max DD (R): медиана = {mc['max_dd_r_p50']}, p95 = {mc['max_dd_r_p95']}",
                f"- Вероятность прибыльности: {mc['prob_profit']:.1%}",
                f"- Бутстреп p-value (H0: E[R] ≤ 0): {mc['bootstrap_p_value']}",
            ]
        else:
            report.append(f"- Невалиден: {mc.get('reason')}")
        report += ["", f"### Вердикт по {symbol}"]
        report += [f"- {c}" for c in checks]
        report += [f"**{'✅ ПРЕИМУЩЕСТВО ПОДТВЕРЖДЕНО' if ok else '❌ ПРЕИМУЩЕСТВО НЕ ДОКАЗАНО'}**", ""]

    n_ok = sum(all_verdicts)
    report += [
        "---",
        "## Итоговый вердикт",
        "```", VERDICT_RULES, "```",
        f"Символов с подтверждённым преимуществом: **{n_ok} из {len(symbols)}**.",
        "",
    ]
    if synthetic:
        report += [
            "На случайных данных корректный результат — 0 из N: система не должна",
            "находить edge там, где его нет. Если выше где-то «✅» — в пайплайне",
            "есть утечка (lookahead/выживание), требуется разбор.",
            "",
            "**Для финального вердикта о реальном преимуществе запустите:**",
            "`python -m scripts.statistical_truth --symbols BTCUSDT,ETHUSDT,SOLUSDT`",
            "на машине с доступом к fapi.binance.com.",
        ]
    else:
        report.append(
            "**ВЕРДИКТ: статистическое преимущество "
            + ("ПОДТВЕРЖДЕНО" if n_ok == len(symbols) else
               "НЕ ДОКАЗАНО — торговля реальными средствами не обоснована.")
            + "**")

    report.append(f"\n_Время выполнения: {time.time() - t0:.0f} c_")
    out.write_text("\n".join(report), encoding="utf-8")
    logger.info("Отчёт: %s", out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--months", type=int, default=24)
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--out", default="STATISTICAL_TRUTH_REPORT.md")
    args = ap.parse_args()
    asyncio.run(run([s.strip().upper() for s in args.symbols.split(",")],
                    args.months, args.synthetic, Path(args.out)))


if __name__ == "__main__":
    main()
