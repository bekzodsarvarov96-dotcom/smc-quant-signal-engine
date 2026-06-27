"""run_backtest — обёртка API над честным движком Statistical Truth:
precompute (capped window) + портфельный симулятор с комиссиями,
slippage, funding и mark-to-market equity. Используется эндпоинтом
POST /api/v1/backtest и автобэктестом."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from ..binance.client import get_client
from ..config import get_settings
from .precompute import precompute_candidates
from .portfolio import CostModel, simulate_portfolio

logger = logging.getLogger(__name__)
settings = get_settings()


async def run_backtest(symbol: str, timeframe: str, days: int,
                       min_score: float | None = None,
                       min_confirmations: int | None = None,
                       allowed_grades: list[str] | None = None) -> dict:
    client = get_client()
    start = datetime.now(timezone.utc) - timedelta(days=days)
    df = await client.klines_history(symbol, timeframe, start)
    if len(df) < 320:
        raise ValueError(f"Недостаточно истории: {len(df)} свечей")

    f = await client.funding_rate_history(symbol, start)
    funding = f.set_index("fundingTime")["fundingRate"] if not f.empty else None

    # funding передаётся в контекст модулей (срез по бару, без look-ahead)
    cands = precompute_candidates(symbol, timeframe, df,
                                  funding_df=f if not f.empty else None)

    # Кэш для Attribution Analysis (GET /api/v1/module-combinations)
    from .attribution import cache_dataset
    cache_dataset(symbol, timeframe, df, cands,
                  funding if funding is not None else None)

    ms = settings.min_signal_score if min_score is None else min_score
    mf = max(3, settings.min_confirmations if min_confirmations is None else min_confirmations)
    grades = allowed_grades or settings.grades
    filtered = [c for c in cands
                if c.score >= ms and c.confirmations >= mf
                and (not grades or c.grade in grades)]

    res = simulate_portfolio(df, filtered, CostModel(funding_series=funding))
    m = res.metrics
    rs = np.array([t.pnl_r for t in res.trades]) if res.trades else np.array([0.0])

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "start": df["open_time"].iloc[0].to_pydatetime().replace(tzinfo=None),
        "end": df["open_time"].iloc[-1].to_pydatetime().replace(tzinfo=None),
        "total_signals": m["n_trades"],
        "wins": int((rs > 0).sum()) if res.trades else 0,
        "losses": int((rs <= 0).sum()) if res.trades else 0,
        "winrate": m["winrate"],
        "total_r": round(float(rs.sum()), 2) if res.trades else 0.0,
        "avg_r": m["expectancy_r"],
        "avg_rr": m["avg_rr"],
        "expected_value": m["expectancy_r"],
        "max_drawdown_r": m["max_drawdown_pct"],   # теперь % equity (mark-to-market)
        "profit_factor": m["profit_factor"] if m["profit_factor"] != float("inf") else 999.0,
        "params": {
            "min_score": ms, "min_families": mf, "grades": grades,
            "costs": {"taker_fee": 0.0005, "slippage_bps": 2.0, "funding": "historical"},
            "sharpe": m["sharpe"], "total_return_pct": m["total_return_pct"],
            "note": "max_drawdown_r содержит % просадки equity (mark-to-market)",
        },
        "trades": [t.__dict__ for t in res.trades][-500:],
    }
