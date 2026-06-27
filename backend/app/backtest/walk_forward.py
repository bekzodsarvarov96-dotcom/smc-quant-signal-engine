"""Walk-Forward Analysis + Out-of-Sample валидация.

Схема: история делится на K последовательных сегментов.
Для k = 1..K−1:
  • train (In-Sample): сегмент k — grid-search порогов (min_score × min_families)
    по expectancy (при ≥ min_trades сделок);
  • test (Out-of-Sample): сегмент k+1 — симуляция с замороженными параметрами.
IS-метрики агрегируются по train-прогонам с выбранными параметрами,
OOS — по test-прогонам. Кандидаты предвычислены, поэтому grid дёшев.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from ..engine.scoring import SignalCandidate
from .portfolio import CostModel, simulate_portfolio, compute_metrics, ClosedTrade

logger = logging.getLogger(__name__)

SCORE_GRID = (20.0, 30.0, 40.0, 50.0)
FAMILY_GRID = (3, 4)
MIN_TRADES_TRAIN = 15


@dataclass
class WFAResult:
    folds: list[dict]
    is_trades: list[ClosedTrade]
    oos_trades: list[ClosedTrade]
    is_metrics: dict
    oos_metrics: dict
    is_equity: pd.Series
    oos_equity: pd.Series


def _filter(cands: list[SignalCandidate], lo: int, hi: int,
            min_score: float, min_fam: int) -> list[SignalCandidate]:
    return [c for c in cands
            if lo <= c.bar_index < hi
            and c.score >= min_score
            and c.confirmations >= min_fam]


def _sim_segment(df: pd.DataFrame, cands: list[SignalCandidate],
                 lo: int, hi: int, costs: CostModel) -> tuple[list[ClosedTrade], pd.Series]:
    seg = df.iloc[lo:hi].reset_index(drop=True)
    shifted = []
    for c in cands:
        c2 = SignalCandidate(**{**c.__dict__})
        c2.bar_index = c.bar_index - lo
        shifted.append(c2)
    res = simulate_portfolio(seg, shifted, costs)
    return res.trades, res.equity_curve


def walk_forward(df: pd.DataFrame, candidates: list[SignalCandidate],
                 costs: CostModel, n_folds: int = 6) -> WFAResult:
    n = len(df)
    edges = [int(n * k / n_folds) for k in range(n_folds + 1)]

    folds = []
    is_trades: list[ClosedTrade] = []
    oos_trades: list[ClosedTrade] = []
    is_eq_parts, oos_eq_parts = [], []

    for k in range(n_folds - 1):
        tr_lo, tr_hi = edges[k], edges[k + 1]
        te_lo, te_hi = edges[k + 1], edges[k + 2]

        best, best_exp = None, -1e9
        for ms in SCORE_GRID:
            for mf in FAMILY_GRID:
                cs = _filter(candidates, tr_lo, tr_hi, ms, mf)
                trades, _ = _sim_segment(df, cs, tr_lo, tr_hi, costs)
                if len(trades) < MIN_TRADES_TRAIN:
                    continue
                exp = sum(t.pnl_r for t in trades) / len(trades)
                if exp > best_exp:
                    best_exp, best = exp, (ms, mf, trades)

        if best is None:   # ничего не наторговали — параметры по умолчанию
            ms, mf = SCORE_GRID[0], FAMILY_GRID[0]
            cs = _filter(candidates, tr_lo, tr_hi, ms, mf)
            tr_trades, tr_eq = _sim_segment(df, cs, tr_lo, tr_hi, costs)
        else:
            ms, mf, tr_trades = best
            _, tr_eq = _sim_segment(df, _filter(candidates, tr_lo, tr_hi, ms, mf),
                                    tr_lo, tr_hi, costs)

        te_cs = _filter(candidates, te_lo, te_hi, ms, mf)
        te_trades, te_eq = _sim_segment(df, te_cs, te_lo, te_hi, costs)

        folds.append({
            "fold": k + 1,
            "train_bars": (tr_lo, tr_hi), "test_bars": (te_lo, te_hi),
            "chosen_min_score": ms, "chosen_min_families": mf,
            "is_trades": len(tr_trades),
            "is_expectancy": round(sum(t.pnl_r for t in tr_trades) / len(tr_trades), 4)
                if tr_trades else 0.0,
            "oos_trades": len(te_trades),
            "oos_expectancy": round(sum(t.pnl_r for t in te_trades) / len(te_trades), 4)
                if te_trades else 0.0,
        })
        is_trades += tr_trades
        oos_trades += te_trades
        is_eq_parts.append(tr_eq)
        oos_eq_parts.append(te_eq)
        logger.info("Fold %d: params(score>=%.0f, fam>=%d) IS n=%d exp=%.3f | OOS n=%d exp=%.3f",
                    k + 1, ms, mf, folds[-1]["is_trades"], folds[-1]["is_expectancy"],
                    folds[-1]["oos_trades"], folds[-1]["oos_expectancy"])

    is_eq = pd.concat(is_eq_parts) if is_eq_parts else pd.Series(dtype=float)
    oos_eq = pd.concat(oos_eq_parts) if oos_eq_parts else pd.Series(dtype=float)
    return WFAResult(
        folds=folds, is_trades=is_trades, oos_trades=oos_trades,
        is_metrics=compute_metrics(is_trades, is_eq, is_eq.iloc[0] if len(is_eq) else 1),
        oos_metrics=compute_metrics(oos_trades, oos_eq, oos_eq.iloc[0] if len(oos_eq) else 1),
        is_equity=is_eq, oos_equity=oos_eq,
    )
