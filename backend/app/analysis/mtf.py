"""Multi Timeframe Analysis.

Определяет тренд старшего ТФ (структура + EMA) и выдаёт:
  • направленный bias по HTF;
  • в ctx.extras["htf_trend"] ∈ {"up","down","range"} — используется движком
    как ЖЁСТКИЙ ФИЛЬТР: входы против старшего тренда запрещены.
"""
from __future__ import annotations

import numpy as np

from .base import AnalysisModule, MarketContext, ModuleResult, find_swings


def _htf_trend(htf_df) -> tuple[str, list[str]]:
    notes: list[str] = []
    if htf_df is None or len(htf_df) < 30:
        return "range", ["Недостаточно данных старшего ТФ"]

    # 1) EMA-наклон
    ema50 = htf_df["close"].ewm(span=50, adjust=False).mean()
    ema200 = htf_df["close"].ewm(span=200, adjust=False).mean() if len(htf_df) >= 210 else None
    price = htf_df["close"].iloc[-1]

    ema_vote = 0
    if ema200 is not None:
        if ema50.iloc[-1] > ema200.iloc[-1] and price > ema200.iloc[-1]:
            ema_vote = 1
        elif ema50.iloc[-1] < ema200.iloc[-1] and price < ema200.iloc[-1]:
            ema_vote = -1
    else:
        slope = ema50.iloc[-1] - ema50.iloc[-10]
        ema_vote = 1 if slope > 0 else -1

    # 2) Структура HTF (последние swing-метки)
    sh, sl = find_swings(htf_df, lookback=2)
    highs = [htf_df["high"].iloc[i] for i in np.where(sh.values)[0][-3:]]
    lows = [htf_df["low"].iloc[i] for i in np.where(sl.values)[0][-3:]]
    struct_vote = 0
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
            struct_vote = 1
        elif highs[-1] < highs[-2] and lows[-1] < lows[-2]:
            struct_vote = -1

    total = ema_vote + struct_vote
    if total >= 1:
        notes.append("HTF: бычий тренд (EMA и/или структура HH+HL)")
        return "up", notes
    if total <= -1:
        notes.append("HTF: медвежий тренд (EMA и/или структура LH+LL)")
        return "down", notes
    notes.append("HTF: флэт / противоречивые признаки")
    return "range", notes


class MultiTimeframeModule(AnalysisModule):
    name = "mtf_trend"
    weight = 1.8   # старший тренд — один из самых весомых факторов

    def analyze(self, ctx: MarketContext) -> ModuleResult:
        trend, notes = _htf_trend(ctx.htf_df)
        ctx.extras["htf_trend"] = trend

        if trend == "up":
            return ModuleResult(self.name, 1.0, 0.8, notes)
        if trend == "down":
            return ModuleResult(self.name, -1.0, 0.8, notes)
        return self.neutral(notes[0])
