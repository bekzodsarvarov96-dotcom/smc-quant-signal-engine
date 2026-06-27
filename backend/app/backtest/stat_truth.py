"""Статистический контур «Statistical Truth v1».

Архитектура (двухпроходная — даёт дешёвый Walk-Forward):
  PASS 1  collect_candidates(): на каждом баре (окно ≤ WINDOW_CAP) движок
          с выключенными фильтрами фиксирует кандидата (score, семейства,
          уровни, флаг counter_htf). 20 модулей считаются один раз.
  PASS 2  select_trades(): фильтры (min_score, ≥3 семейства, HTF, cooldown,
          грейды) применяются к сохранённым кандидатам без пересчёта.
  SIM     simulate_portfolio(): издержки (комиссии, слиппедж, funding),
          перекрывающиеся позиции, портфельная equity-кривая (realized +
          mark-to-market), истинный Max Drawdown, Sharpe.
  STATS   monte_carlo(), walk_forward(), is_oos_report().

Единицы: R (1R = расстояние entry→SL). Допущение: фиксированный риск 1R
на сделку без компаундирования (отчёт в R-метриках).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd

from ..analysis.base import MarketContext
from ..config import get_settings
from ..engine.scoring import evaluate, grade_of

settings = get_settings()

WINDOW_CAP = 450          # P0-3: кап окна walk-forward
WARMUP = 250
MAX_HOLD = 96
PARTIALS = (0.5, 0.3, 0.2)
FUNDING_PERIOD_BARS = {"15m": 32, "30m": 16, "1h": 8, "4h": 2}  # баров между funding


# ---------------------------------------------------------------- PASS 1

@dataclass
class Candidate:
    bar: int
    direction: str
    score: float
    families: int
    counter_htf: bool
    htf_aligned: bool
    entry: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float


def _resample_htf(df: pd.DataFrame, timeframe: str) -> pd.DataFrame | None:
    rule = {"5m": "1h", "15m": "4h", "30m": "4h", "1h": "4h", "4h": "1D"}.get(timeframe)
    if rule is None:
        return None
    g = df.set_index("open_time").resample(rule, label="left", closed="left")
    return pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(),
        "low": g["low"].min(), "close": g["close"].last(),
        "volume": g["volume"].sum(),
    }).dropna().reset_index()


def collect_candidates(df: pd.DataFrame, symbol: str, timeframe: str,
                       progress_every: int = 0) -> list[Candidate]:
    out: list[Candidate] = []
    for i in range(WARMUP, len(df) - 2):
        window = df.iloc[max(0, i + 1 - WINDOW_CAP): i + 1].reset_index(drop=True)
        htf = _resample_htf(window, timeframe)
        ctx = MarketContext(symbol=symbol, timeframe=timeframe, df=window, htf_df=htf)
        cand = evaluate(ctx, min_score=0, min_families=0,
                        allowed_grades=["A+", "A", "B", "C", "D"],
                        enforce_htf=False)
        if cand is None:
            continue
        htf_aligned = (cand.direction == "LONG" and cand.htf_trend == "up") or \
                      (cand.direction == "SHORT" and cand.htf_trend == "down")
        out.append(Candidate(
            bar=i, direction=cand.direction, score=cand.score,
            families=cand.confirmations, counter_htf=cand.counter_htf,
            htf_aligned=htf_aligned,
            entry=cand.entry, stop_loss=cand.stop_loss,
            tp1=cand.tp1, tp2=cand.tp2, tp3=cand.tp3,
        ))
        if progress_every and len(out) % progress_every == 0:
            print(f"    …{i}/{len(df)} баров, кандидатов: {len(out)}")
    return out


# ---------------------------------------------------------------- PASS 2

@dataclass
class SelectionParams:
    min_score: float = 50.0
    min_families: int = 3
    block_counter_htf: bool = True
    require_htf_aligned: bool = False
    cooldown_bars: int = 8
    allowed_grades: tuple = ("A+", "A", "B")
    grade_thresholds: dict | None = None


def select_trades(cands: list[Candidate], params: SelectionParams,
                  bar_range: tuple[int, int] | None = None) -> list[Candidate]:
    selected: list[Candidate] = []
    last_bar = -10 ** 9
    for c in cands:
        if bar_range and not (bar_range[0] <= c.bar < bar_range[1]):
            continue
        if c.score < params.min_score or c.families < params.min_families:
            continue
        if params.block_counter_htf and c.counter_htf:
            continue
        if params.require_htf_aligned and not c.htf_aligned:
            continue
        g = grade_of(c.score, c.families, c.htf_aligned, params.grade_thresholds)
        if g not in params.allowed_grades:
            continue
        if c.bar - last_bar < params.cooldown_bars:
            continue
        selected.append(c)
        last_bar = c.bar
    return selected


# ---------------------------------------------------------------- SIM

@dataclass
class SimTrade:
    bar_entry: int
    direction: str
    entry: float
    risk: float
    fills: list = field(default_factory=list)   # (bar, fraction, price)
    gross_r: float = 0.0
    fees_r: float = 0.0
    funding_r: float = 0.0
    net_r: float = 0.0
    result: str = ""


def _exec_price(price: float, side_buy: bool) -> float:
    slip = price * settings.slippage_bps / 10_000
    return price + slip if side_buy else price - slip


def simulate_trade(df: pd.DataFrame, c: Candidate,
                   funding: pd.Series | None,
                   funding_step: int) -> SimTrade:
    long = c.direction == "LONG"
    entry = _exec_price(c.entry, side_buy=long)
    risk = abs(c.entry - c.stop_loss)
    t = SimTrade(bar_entry=c.bar, direction=c.direction, entry=entry, risk=risk)

    fee = settings.taker_fee
    t.fees_r += fee * entry / risk                       # комиссия входа (полный объём)

    tps = [c.tp1, c.tp2, c.tp3]
    hit = [False, False, False]
    remaining, cur_sl = 1.0, c.stop_loss
    end_bar = min(c.bar + MAX_HOLD, len(df) - 1)

    def close_part(bar: int, frac: float, raw_price: float):
        nonlocal remaining
        px = _exec_price(raw_price, side_buy=not long)
        r = (px - entry) / risk if long else (entry - px) / risk
        t.gross_r += frac * r
        t.fees_r += fee * px / risk * frac
        t.fills.append((bar, frac, px))
        remaining -= frac

    for j in range(c.bar + 1, end_bar + 1):
        bar = df.iloc[j]
        # funding: списываем на барах funding-расчёта на остаток позиции
        if funding is not None and settings.include_funding_costs \
                and j % funding_step == 0 and remaining > 0:
            rate = float(funding.iloc[min(j // funding_step, len(funding) - 1)])
            pay = rate if long else -rate                 # лонг платит положительный funding
            t.funding_r += pay * bar["close"] / risk * remaining

        sl_hit = bar["low"] <= cur_sl if long else bar["high"] >= cur_sl
        if sl_hit:                                        # консервативно: SL раньше TP
            close_part(j, remaining, cur_sl)
            t.result = "LOSS" if not hit[0] else "WIN"
            break
        for k, tp in enumerate(tps):
            if hit[k]:
                continue
            if (bar["high"] >= tp) if long else (bar["low"] <= tp):
                close_part(j, PARTIALS[k], tp)
                hit[k] = True
                if k == 0:
                    cur_sl = entry                        # безубыток после TP1
        if hit[2]:
            t.result = "WIN"
            break

    if remaining > 1e-9:                                  # таймаут
        close_part(end_bar, remaining, float(df["close"].iloc[end_bar]))
        t.result = t.result or ("WIN" if t.gross_r > 0 else "LOSS")

    t.net_r = t.gross_r - t.fees_r - t.funding_r
    if not t.result:
        t.result = "WIN" if t.net_r > 0 else "LOSS"
    return t


def simulate_portfolio(df: pd.DataFrame, selected: list[Candidate],
                       funding: pd.Series | None, timeframe: str) -> dict:
    fstep = FUNDING_PERIOD_BARS.get(timeframe, 8)
    trades = [simulate_trade(df, c, funding, fstep) for c in selected]

    # --- Портфельная equity-кривая (realized + mark-to-market), R-единицы ---
    n = len(df)
    realized = np.zeros(n)
    for t in trades:
        cost_per_bar_done = False
        for (bar, frac, px) in t.fills:
            long = t.direction == "LONG"
            r = (px - t.entry) / t.risk if long else (t.entry - px) / t.risk
            realized[bar] += frac * r
        # издержки списываем в бар последнего филла (упрощение учёта)
        last_bar = t.fills[-1][0] if t.fills else t.bar_entry
        realized[last_bar] -= (t.fees_r + t.funding_r)

    equity = np.zeros(n)
    cum_realized = np.cumsum(realized)
    # mark-to-market открытых остатков
    open_legs: list[tuple[int, int, float, str, float, float]] = []
    for t in trades:
        rem, prev_bar = 1.0, t.bar_entry
        for (bar, frac, _) in t.fills:
            if rem > 1e-9:
                open_legs.append((prev_bar, bar, rem, t.direction, t.entry, t.risk))
            rem -= frac
            prev_bar = bar
    closes = df["close"].values
    mtm = np.zeros(n)
    for (b0, b1, frac, direction, entry, risk) in open_legs:
        sgn = 1.0 if direction == "LONG" else -1.0
        seg = slice(b0, b1)
        mtm[seg] += frac * sgn * (closes[seg] - entry) / risk
    equity = cum_realized + mtm

    # --- Метрики ---
    rs = np.array([t.net_r for t in trades])
    wins, losses = rs[rs > 0], rs[rs <= 0]
    gross_w, gross_l = wins.sum(), abs(losses.sum())

    peak = np.maximum.accumulate(equity)
    dd = peak - equity
    max_dd = float(dd.max()) if n else 0.0

    diffs = np.diff(equity)
    bars_per_year = {"15m": 35040, "30m": 17520, "1h": 8760, "4h": 2190}.get(timeframe, 8760)
    sharpe = float(diffs.mean() / diffs.std() * math.sqrt(bars_per_year)) \
        if diffs.std() > 0 else 0.0

    max_concurrent = 0
    if trades:
        events = []
        for t in trades:
            end = t.fills[-1][0] if t.fills else t.bar_entry
            events += [(t.bar_entry, 1), (end, -1)]
        cur = 0
        for _, e in sorted(events):
            cur += e
            max_concurrent = max(max_concurrent, cur)

    return {
        "trades": trades,
        "equity": equity,
        "n_trades": len(trades),
        "winrate": round(len(wins) / len(rs) * 100, 2) if len(rs) else 0.0,
        "profit_factor": round(gross_w / gross_l, 3) if gross_l > 0 else float(len(wins) > 0),
        "expectancy": round(float(rs.mean()), 4) if len(rs) else 0.0,
        "avg_rr": round(float(wins.mean()), 3) if len(wins) else 0.0,
        "total_r": round(float(rs.sum()), 2),
        "max_drawdown_r": round(max_dd, 2),
        "sharpe": round(sharpe, 2),
        "avg_costs_r": round(float(np.mean([t.fees_r + t.funding_r for t in trades])), 4)
        if trades else 0.0,
        "max_concurrent": max_concurrent,
    }


# ---------------------------------------------------------------- STATS

def monte_carlo(trades: list[SimTrade], n_sims: int = 1000, seed: int = 42) -> dict:
    """Bootstrap-ресемплинг сделок (iid): доверительные интервалы и P(edge)."""
    rs = np.array([t.net_r for t in trades])
    if len(rs) < 5:
        return {"error": "слишком мало сделок для Monte Carlo"}
    rng = np.random.default_rng(seed)
    totals, dds, evs = [], [], []
    for _ in range(n_sims):
        sample = rng.choice(rs, size=len(rs), replace=True)
        eq = np.cumsum(sample)
        totals.append(eq[-1])
        dds.append(float((np.maximum.accumulate(eq) - eq).max()))
        evs.append(sample.mean())
    totals, dds, evs = map(np.array, (totals, dds, evs))
    return {
        "p_profit": round(float((totals > 0).mean()), 3),
        "expectancy_ci95": [round(float(np.percentile(evs, 2.5)), 4),
                            round(float(np.percentile(evs, 97.5)), 4)],
        "total_r_ci95": [round(float(np.percentile(totals, 2.5)), 2),
                         round(float(np.percentile(totals, 97.5)), 2)],
        "max_dd_p95": round(float(np.percentile(dds, 95)), 2),
    }


def calibrate_thresholds(cands: list[Candidate],
                         bar_range: tuple[int, int]) -> dict:
    """Грейд-пороги = перцентили фактического распределения score на IS."""
    scores = np.array([c.score for c in cands
                       if bar_range[0] <= c.bar < bar_range[1]])
    if len(scores) < 50:
        from ..engine.scoring import DEFAULT_GRADE_THRESHOLDS
        return dict(DEFAULT_GRADE_THRESHOLDS)
    return {
        "A+": round(float(np.percentile(scores, 98)), 1),
        "A": round(float(np.percentile(scores, 90)), 1),
        "B": round(float(np.percentile(scores, 70)), 1),
        "C": round(float(np.percentile(scores, 40)), 1),
    }


def _optimize_min_score(df, cands, funding, timeframe,
                        bar_range, thresholds) -> float:
    """Перебор порога score на IS: максимизация expectancy при ≥20 сделках."""
    scores = np.array([c.score for c in cands
                       if bar_range[0] <= c.bar < bar_range[1]])
    if len(scores) < 50:
        return 50.0
    best, best_ev = float(np.percentile(scores, 50)), -1e9
    for pct in (40, 55, 70, 80):
        ms = float(np.percentile(scores, pct))
        params = SelectionParams(min_score=ms, grade_thresholds=thresholds)
        sel = select_trades(cands, params, bar_range)
        if len(sel) < 20:
            continue
        res = simulate_portfolio(df, sel, funding, timeframe)
        if res["expectancy"] > best_ev:
            best_ev, best = res["expectancy"], ms
    return round(best, 1)


def walk_forward(df: pd.DataFrame, cands: list[Candidate],
                 funding: pd.Series | None, timeframe: str,
                 n_folds: int = 6) -> list[dict]:
    """Anchored walk-forward: калибровка на [0..k), тест на сегменте k."""
    n = len(df)
    seg = (n - WARMUP) // n_folds
    folds = []
    for k in range(1, n_folds):
        is_range = (WARMUP, WARMUP + k * seg)
        oos_range = (WARMUP + k * seg, WARMUP + (k + 1) * seg)
        thr = calibrate_thresholds(cands, is_range)
        ms = _optimize_min_score(df, cands, funding, timeframe, is_range, thr)
        params = SelectionParams(min_score=ms, grade_thresholds=thr)
        sel = select_trades(cands, params, oos_range)
        res = simulate_portfolio(df, sel, funding, timeframe)
        folds.append({
            "fold": k, "min_score": ms, "thresholds": thr,
            "oos_bars": oos_range,
            "n_trades": res["n_trades"], "winrate": res["winrate"],
            "expectancy": res["expectancy"], "profit_factor": res["profit_factor"],
            "total_r": res["total_r"],
        })
    return folds


def is_oos_report(df: pd.DataFrame, cands: list[Candidate],
                  funding: pd.Series | None, timeframe: str,
                  oos_fraction: float = 0.25) -> dict:
    """Главный сплит: первые 75% — In-Sample (калибровка), последние 25% — OOS."""
    n = len(df)
    split = WARMUP + int((n - WARMUP) * (1 - oos_fraction))
    is_range, oos_range = (WARMUP, split), (split, n)

    thr = calibrate_thresholds(cands, is_range)
    ms = _optimize_min_score(df, cands, funding, timeframe, is_range, thr)
    params = SelectionParams(min_score=ms, grade_thresholds=thr)

    out = {"calibration": {"grade_thresholds": thr, "min_score": ms,
                           "min_families": params.min_families}}
    for label, rng in (("in_sample", is_range), ("out_of_sample", oos_range)):
        sel = select_trades(cands, params, rng)
        res = simulate_portfolio(df, sel, funding, timeframe)
        mc = monte_carlo(res["trades"])
        out[label] = {k: v for k, v in res.items() if k not in ("trades", "equity")}
        out[label]["monte_carlo"] = mc
        out[label]["_trades"] = res["trades"]
        out[label]["_equity"] = res["equity"]
    return out


def verdict(report_oos: dict) -> tuple[str, list[str]]:
    """Формальный вердикт о статистическом преимуществе по OOS-данным."""
    notes = []
    n = report_oos.get("n_trades", 0)
    ev = report_oos.get("expectancy", 0.0)
    pf = report_oos.get("profit_factor", 0.0)
    mc = report_oos.get("monte_carlo", {})
    ci = mc.get("expectancy_ci95", [0, 0])
    p_profit = mc.get("p_profit", 0.0)

    if n < 30:
        return ("НЕДОСТАТОЧНО ДАННЫХ",
                [f"OOS-сделок всего {n} (<30) — вывод статистически не значим"])
    if ci[0] > 0 and pf >= 1.15 and p_profit >= 0.95:
        notes.append(f"95% CI expectancy [{ci[0]}; {ci[1]}] R — нижняя граница > 0")
        notes.append(f"PF={pf}, P(прибыль)={p_profit:.0%}, n={n}")
        return ("ПРЕИМУЩЕСТВО ПОДТВЕРЖДЕНО (на данном периоде/рынке)", notes)
    if ev > 0 and ci[0] <= 0:
        notes.append(f"EV={ev}R > 0, но 95% CI [{ci[0]}; {ci[1]}] включает 0")
        return ("ПРЕИМУЩЕСТВО НЕ ДОКАЗАНО (положительное, но незначимое)", notes)
    notes.append(f"EV={ev}R, PF={pf}, CI=[{ci[0]}; {ci[1]}]")
    return ("ПРЕИМУЩЕСТВА НЕТ", notes)
