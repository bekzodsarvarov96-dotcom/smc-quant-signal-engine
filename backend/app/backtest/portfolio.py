"""Портфельный симулятор с реальными издержками (Statistical Truth v1).

Моделирует:
  • комиссии Binance USDT-M Futures (taker, обе стороны, по умолчанию 0.05%);
  • slippage (б.п. от цены, обе стороны);
  • funding payments каждые 8 часов на открытый нотционал;
  • перекрывающиеся позиции (до max_concurrent одновременно);
  • риск-сайзинг: фиксированный % equity на риск сделки;
  • mark-to-market equity-кривую по каждому бару → истинный Max Drawdown
    и Sharpe по дневным доходностям.

Допущения (консервативные): входы/SL — taker по рынку со slippage;
TP — лимитные, но тарифицируются как taker; при касании SL и TP в одном
баре исполняется SL; funding в синтетическом режиме = const, в реальном —
из исторического ряда fundingRate.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..engine.scoring import SignalCandidate

PARTIALS = (0.5, 0.3, 0.2)
MAX_HOLD = 96


@dataclass
class CostModel:
    taker_fee: float = 0.0005        # 0.05% за сторону
    slippage_bps: float = 2.0        # 2 б.п. за сторону
    funding_rate_const: float = 0.0001   # 0.01%/8ч, если нет истории
    funding_series: pd.Series | None = None  # index=Timestamp, value=rate

    def slip(self) -> float:
        return self.slippage_bps / 10_000.0

    def funding_at(self, ts: pd.Timestamp) -> float:
        if self.funding_series is None or self.funding_series.empty:
            return self.funding_rate_const
        idx = self.funding_series.index.searchsorted(ts)
        idx = min(max(idx, 0), len(self.funding_series) - 1)
        return float(self.funding_series.iloc[idx])


@dataclass
class Position:
    cand: SignalCandidate
    open_bar: int
    entry: float          # фактическая цена входа (со slippage)
    qty: float
    risk_amount: float    # $ риска (на 1.0 позиции)
    cur_sl: float
    remaining: float = 1.0
    hits: list = field(default_factory=lambda: [False, False, False])
    realized_pnl: float = 0.0   # $ с учётом комиссий выходов
    fees_paid: float = 0.0
    funding_paid: float = 0.0

    @property
    def long(self) -> bool:
        return self.cand.direction == "LONG"


@dataclass
class ClosedTrade:
    symbol: str
    direction: str
    grade: str
    score: float
    open_time: str
    close_time: str
    entry: float
    exit_avg: float
    pnl: float            # $ после ВСЕХ издержек
    pnl_r: float          # в R от risk_amount
    fees: float
    funding: float
    bars_held: int
    result: str
    modules: dict = field(default_factory=dict)   # {name: вклад в сторону сигнала}


@dataclass
class PortfolioResult:
    trades: list[ClosedTrade]
    equity_curve: pd.Series          # index = open_time, mark-to-market
    initial_equity: float
    metrics: dict = field(default_factory=dict)


def _close_part(pos: Position, price: float, part: float, costs: CostModel,
                is_market: bool) -> float:
    """Закрывает долю part позиции по price; возвращает $-pnl части (с комиссией)."""
    px = price * (1 - costs.slip()) if (pos.long and is_market) else \
         price * (1 + costs.slip()) if (not pos.long and is_market) else price
    qty_part = pos.qty * part
    gross = (px - pos.entry) * qty_part if pos.long else (pos.entry - px) * qty_part
    fee = px * qty_part * costs.taker_fee
    pos.fees_paid += fee
    pos.remaining -= part
    pnl = gross - fee
    pos.realized_pnl += pnl
    return pnl


def simulate_portfolio(df: pd.DataFrame,
                       candidates: list[SignalCandidate],
                       costs: CostModel,
                       initial_equity: float = 10_000.0,
                       risk_pct: float = 0.01,
                       max_concurrent: int = 3,
                       cooldown_bars: int = 8) -> PortfolioResult:
    cand_by_bar: dict[int, SignalCandidate] = {}
    for c in candidates:
        cand_by_bar.setdefault(c.bar_index, c)   # 1 кандидат на бар

    cash = initial_equity
    open_pos: list[Position] = []
    closed: list[ClosedTrade] = []
    equity_hist = np.zeros(len(df))
    last_open_bar = -10**9

    times = df["open_time"]
    highs, lows, closes = df["high"].values, df["low"].values, df["close"].values
    hours = times.dt.hour.values if hasattr(times, "dt") else np.zeros(len(df), int)

    def unrealized(pos: Position, price: float) -> float:
        q = pos.qty * pos.remaining
        return (price - pos.entry) * q if pos.long else (pos.entry - price) * q

    for j in range(len(df)):
        hi, lo, cl = highs[j], lows[j], closes[j]
        ts = times.iloc[j]

        # ----- funding каждые 8 часов -----
        if hours[j] % 8 == 0 and j > 0 and hours[j] != hours[j - 1]:
            rate = costs.funding_at(ts)
            for pos in open_pos:
                notional = pos.qty * pos.remaining * cl
                pay = rate * notional * (1 if pos.long else -1)  # long платит при rate>0
                pos.funding_paid += pay
                pos.realized_pnl -= pay
                cash -= pay

        # ----- обработка открытых позиций -----
        still_open: list[Position] = []
        for pos in open_pos:
            done = False
            sl_hit = (lo <= pos.cur_sl) if pos.long else (hi >= pos.cur_sl)
            if sl_hit:                                   # SL приоритетнее TP
                pnl = _close_part(pos, pos.cur_sl, pos.remaining, costs, is_market=True)
                cash += pnl + 0  # pnl уже нетто
                done = True
            else:
                tps = (pos.cand.tp1, pos.cand.tp2, pos.cand.tp3)
                for k, tp in enumerate(tps):
                    if pos.hits[k]:
                        continue
                    tp_hit = (hi >= tp) if pos.long else (lo <= tp)
                    if tp_hit:
                        part = min(PARTIALS[k], pos.remaining)
                        cash += _close_part(pos, tp, part, costs, is_market=False)
                        pos.hits[k] = True
                        if k == 0:
                            pos.cur_sl = pos.entry       # безубыток
                if pos.remaining <= 1e-9 or pos.hits[2]:
                    if pos.remaining > 1e-9:
                        cash += _close_part(pos, cl, pos.remaining, costs, is_market=True)
                    done = True
                elif j - pos.open_bar >= MAX_HOLD:       # таймаут
                    cash += _close_part(pos, cl, pos.remaining, costs, is_market=True)
                    done = True

            if done:
                pnl_total = pos.realized_pnl
                closed.append(ClosedTrade(
                    symbol=pos.cand.symbol, direction=pos.cand.direction,
                    grade=pos.cand.grade, score=pos.cand.score,
                    open_time=str(times.iloc[pos.open_bar]), close_time=str(ts),
                    entry=pos.entry,
                    exit_avg=pos.entry + pnl_total / max(pos.qty, 1e-12) * (1 if pos.long else -1),
                    pnl=round(pnl_total, 4),
                    pnl_r=round(pnl_total / pos.risk_amount, 4),
                    fees=round(pos.fees_paid, 4),
                    funding=round(pos.funding_paid, 4),
                    bars_held=j - pos.open_bar,
                    result="WIN" if pnl_total > 0 else "LOSS",
                    modules=pos.cand.module_scores,
                ))
            else:
                still_open.append(pos)
        open_pos = still_open

        # ----- equity mark-to-market -----
        equity = cash + sum(unrealized(p, cl) for p in open_pos)
        equity_hist[j] = equity

        # ----- новый вход -----
        cand = cand_by_bar.get(j)
        if cand and len(open_pos) < max_concurrent and j - last_open_bar >= cooldown_bars \
                and equity > 0:
            slip = costs.slip()
            entry = cand.entry * (1 + slip) if cand.direction == "LONG" else cand.entry * (1 - slip)
            risk_per_unit = abs(entry - cand.stop_loss)
            if risk_per_unit > 0:
                risk_amount = equity * risk_pct
                qty = risk_amount / risk_per_unit
                fee_in = entry * qty * costs.taker_fee
                cash -= fee_in
                pos = Position(cand=cand, open_bar=j, entry=entry, qty=qty,
                               risk_amount=risk_amount, cur_sl=cand.stop_loss)
                pos.fees_paid += fee_in
                pos.realized_pnl -= fee_in
                open_pos.append(pos)
                last_open_bar = j

    # закрыть хвост по последней цене
    for pos in open_pos:
        cash += _close_part(pos, closes[-1], pos.remaining, costs, is_market=True)
        closed.append(ClosedTrade(
            symbol=pos.cand.symbol, direction=pos.cand.direction,
            grade=pos.cand.grade, score=pos.cand.score,
            open_time=str(times.iloc[pos.open_bar]), close_time=str(times.iloc[-1]),
            entry=pos.entry, exit_avg=closes[-1],
            pnl=round(pos.realized_pnl, 4), pnl_r=round(pos.realized_pnl / pos.risk_amount, 4),
            fees=round(pos.fees_paid, 4), funding=round(pos.funding_paid, 4),
            bars_held=len(df) - 1 - pos.open_bar, result="WIN" if pos.realized_pnl > 0 else "LOSS",
            modules=pos.cand.module_scores,
        ))
    equity_hist[-1] = cash

    eq = pd.Series(equity_hist, index=times.values)
    return PortfolioResult(trades=closed, equity_curve=eq,
                           initial_equity=initial_equity,
                           metrics=compute_metrics(closed, eq, initial_equity))


def compute_metrics(trades: list[ClosedTrade], equity: pd.Series,
                    initial: float) -> dict:
    if not trades:
        return {"n_trades": 0, "winrate": 0.0, "profit_factor": 0.0,
                "expectancy_r": 0.0, "avg_rr": 0.0, "sharpe": 0.0,
                "max_drawdown_pct": 0.0, "total_return_pct": 0.0,
                "total_fees": 0.0, "total_funding": 0.0}

    rs = np.array([t.pnl_r for t in trades])
    wins = rs[rs > 0]
    losses = rs[rs <= 0]
    gross_w, gross_l = wins.sum(), abs(losses.sum())

    # Истинный Max DD по mark-to-market equity
    roll_max = equity.cummax()
    dd = (equity - roll_max) / roll_max
    max_dd = float(-dd.min() * 100)

    # Sharpe по дневным доходностям, годовая (365)
    daily = equity.resample("1D").last().pct_change().dropna() \
        if isinstance(equity.index, pd.DatetimeIndex) else pd.Series(dtype=float)
    sharpe = float(daily.mean() / daily.std() * np.sqrt(365)) \
        if len(daily) > 10 and daily.std() > 0 else 0.0

    return {
        "n_trades": len(trades),
        "winrate": round(len(wins) / len(trades) * 100, 2),
        "profit_factor": round(gross_w / gross_l, 3) if gross_l > 0 else float("inf"),
        "expectancy_r": round(rs.mean(), 4),
        "avg_rr": round(wins.mean(), 3) if len(wins) else 0.0,
        "sharpe": round(sharpe, 3),
        "max_drawdown_pct": round(max_dd, 2),
        "total_return_pct": round((equity.iloc[-1] / initial - 1) * 100, 2),
        "total_fees": round(sum(t.fees for t in trades), 2),
        "total_funding": round(sum(t.funding for t in trades), 2),
    }
