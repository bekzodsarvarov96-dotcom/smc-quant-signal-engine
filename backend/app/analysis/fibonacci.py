"""Fibonacci Retracement по последнему импульсу (swing low → swing high и наоборот).

Зоны интереса: 0.5, 0.618, 0.705 (OTE), 0.786.
Если цена корректируется в одну из зон по направлению тренда — подтверждение.
"""
from __future__ import annotations

from .base import AnalysisModule, MarketContext, ModuleResult, get_swing_points, cached_atr
from .market_structure import detect_structure

FIB_LEVELS = (0.5, 0.618, 0.705, 0.786)


class FibonacciModule(AnalysisModule):
    name = "fibonacci"
    weight = 1.0

    def analyze(self, ctx: MarketContext) -> ModuleResult:
        df = ctx.df
        swings = get_swing_points(ctx)
        st = detect_structure(ctx)
        if not swings["highs"] or not swings["lows"]:
            return self.neutral()

        price = df["close"].iloc[-1]
        tol = cached_atr(ctx).iloc[-1] * 0.35

        hi_idx, hi = swings["highs"][-1]
        lo_idx, lo = swings["lows"][-1]
        if hi == lo:
            return self.neutral()

        if st["trend"] == "up" and lo_idx < hi_idx:
            # Импульс вверх lo→hi, коррекция вниз: уровни от hi
            for lvl in FIB_LEVELS:
                target = hi - (hi - lo) * lvl
                if abs(price - target) <= tol:
                    return ModuleResult(
                        self.name, 1.0, 0.7,
                        [f"Откат к Fibonacci {lvl} ({target:.6g}) в восходящем импульсе"],
                    )
        if st["trend"] == "down" and hi_idx < lo_idx:
            for lvl in FIB_LEVELS:
                target = lo + (hi - lo) * lvl
                if abs(price - target) <= tol:
                    return ModuleResult(
                        self.name, -1.0, 0.7,
                        [f"Откат к Fibonacci {lvl} ({target:.6g}) в нисходящем импульсе"],
                    )
        return self.neutral()
