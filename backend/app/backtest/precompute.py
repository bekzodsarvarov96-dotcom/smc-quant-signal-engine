"""Предвычисление кандидатов: один дорогой проход по истории.

Все пороги (score / семейства / грейды) применяются ПОСЛЕ, при симуляции —
это позволяет делать walk-forward grid-search без повторного анализа.
Окно анализа ограничено WINDOW барами (исправление P0-3: O(n²) → O(n·W)).
"""
from __future__ import annotations

import logging

import pandas as pd

from ..analysis.base import MarketContext
from ..engine.scoring import evaluate, SignalCandidate

logger = logging.getLogger(__name__)

WINDOW = 600     # хвост истории, доступный модулям
WARMUP = 260

_HTF_RULE = {"1m": "15min", "5m": "1h", "15m": "4h", "30m": "4h", "1h": "4h", "4h": "1D"}


def _resample_htf(df: pd.DataFrame, timeframe: str) -> pd.DataFrame | None:
    rule = _HTF_RULE.get(timeframe)
    if rule is None:
        return None
    g = df.set_index("open_time").resample(rule, label="left", closed="left")
    return pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(),
        "low": g["low"].min(), "close": g["close"].last(),
        "volume": g["volume"].sum(),
    }).dropna().reset_index()


def precompute_candidates(symbol: str, timeframe: str, df: pd.DataFrame,
                          enforce_htf: bool = True,
                          progress_every: int = 2000,
                          funding_df: pd.DataFrame | None = None) -> list[SignalCandidate]:
    """Кандидаты без порогов: min_score=0, families=0, все грейды.

    funding_df (колонки fundingTime, fundingRate): на каждом баре модулю
    передаётся ТОЛЬКО история с fundingTime <= времени открытия текущего бара
    (без look-ahead), хвост 30 записей — идентично live-сканеру (limit=30).
    """
    out: list[SignalCandidate] = []
    n = len(df)

    funding_times = None
    if funding_df is not None and not funding_df.empty:
        funding_df = funding_df.sort_values("fundingTime").reset_index(drop=True)
        funding_times = funding_df["fundingTime"].values
    bar_times = df["open_time"].values

    for i in range(WARMUP, n - 1):
        lo = max(0, i + 1 - WINDOW)
        window = df.iloc[lo: i + 1].reset_index(drop=True)
        htf = _resample_htf(window, timeframe)

        funding_slice = None
        if funding_times is not None:
            k = funding_times.searchsorted(bar_times[i], side="right")
            if k > 0:
                funding_slice = funding_df.iloc[max(0, k - 30): k]

        ctx = MarketContext(symbol=symbol, timeframe=timeframe, df=window,
                            htf_df=htf, funding=funding_slice)
        cand = evaluate(ctx, min_score=0, min_confirmations=0,
                        allowed_grades=["A+", "A", "B", "C", "D"],
                        enforce_htf=enforce_htf)
        if cand is not None:
            cand.bar_index = i
            out.append(cand)
        if progress_every and i % progress_every == 0:
            logger.info("%s precompute: %d/%d (%d кандидатов)", symbol, i, n, len(out))
    return out
