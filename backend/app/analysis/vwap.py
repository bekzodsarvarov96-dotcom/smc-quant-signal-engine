"""VWAP (rolling, по окну сессии) с полосами стандартного отклонения.

  • Цена выше VWAP — бычий контекст; ниже — медвежий.
  • Тест VWAP по тренду — точка входа (mean reversion to fair price).
  • Выход за ±2σ — растяжение, контекст возврата (мягкий контрсигнал).
"""
from __future__ import annotations

import numpy as np

from .base import AnalysisModule, MarketContext, ModuleResult, cached_atr

WINDOW = 96  # ~1 сутки на 15m


class VWAPModule(AnalysisModule):
    name = "vwap"
    weight = 1.0

    def analyze(self, ctx: MarketContext) -> ModuleResult:
        df = ctx.df
        if len(df) < WINDOW + 5:
            return self.neutral()

        w = df.iloc[-WINDOW:]
        tp = (w["high"] + w["low"] + w["close"]) / 3
        vol = w["volume"].replace(0, np.nan)
        vwap = float((tp * w["volume"]).sum() / w["volume"].sum())
        dev = float(np.sqrt(((tp - vwap) ** 2 * w["volume"]).sum() / w["volume"].sum()))

        price = df["close"].iloc[-1]
        last_low, last_high = df["low"].iloc[-1], df["high"].iloc[-1]
        tol = cached_atr(ctx).iloc[-1] * 0.3
        htf = ctx.extras.get("htf_trend", "range")
        ctx.extras["vwap"] = {"vwap": vwap, "dev": dev}

        # Тест VWAP по тренду
        if htf == "up" and last_low <= vwap + tol and price > vwap:
            return ModuleResult(self.name, 1.0, 0.75,
                                [f"Откат к VWAP {vwap:.6g} в аптренде и удержание выше"])
        if htf == "down" and last_high >= vwap - tol and price < vwap:
            return ModuleResult(self.name, -1.0, 0.75,
                                [f"Откат к VWAP {vwap:.6g} в даунтренде и удержание ниже"])

        # Растяжение за 2σ — против движения
        if dev > 0:
            z = (price - vwap) / dev
            if z >= 2.2:
                return ModuleResult(self.name, -0.5, 0.45,
                                    [f"Цена растянута на {z:.1f}σ выше VWAP — риск возврата"])
            if z <= -2.2:
                return ModuleResult(self.name, 0.5, 0.45,
                                    [f"Цена растянута на {abs(z):.1f}σ ниже VWAP — риск возврата"])

        # Базовый контекст
        if price > vwap:
            return ModuleResult(self.name, 0.4, 0.35, [f"Цена выше VWAP {vwap:.6g}"])
        return ModuleResult(self.name, -0.4, 0.35, [f"Цена ниже VWAP {vwap:.6g}"])
