"""Market Structure Shift (MSS).

Строже, чем CHOCH: слом структуры засчитывается только если
  1) перед сломом был liquidity sweep (снятие экстремума хвостом), и
  2) слом произошёл импульсной свечой (displacement > 1.2 ATR телом).
Классическая SMC-последовательность: sweep → displacement → MSS.
"""
from __future__ import annotations

from .base import AnalysisModule, MarketContext, ModuleResult, cached_atr, get_swing_points


class MSSModule(AnalysisModule):
    name = "mss"
    weight = 1.6

    def analyze(self, ctx: MarketContext) -> ModuleResult:
        df = ctx.df
        if len(df) < 30:
            return self.neutral()
        swings = get_swing_points(ctx)
        a = cached_atr(ctx).iloc[-1]

        last3 = df.iloc[-3:]
        body = (last3["close"] - last3["open"]).iloc[-1]
        displacement_up = body > 1.2 * a
        displacement_down = -body > 1.2 * a

        prev_lows = [p for i, p in swings["lows"] if i < len(df) - 1][-4:]
        prev_highs = [p for i, p in swings["highs"] if i < len(df) - 1][-4:]
        close = df["close"].iloc[-1]

        lows_a = df["low"].values[-6:]
        highs_a = df["high"].values[-6:]
        closes_a = df["close"].values[-6:]
        # Bullish MSS: недавний sweep лоя + импульсный пробой ближайшего LH
        swept_low = any(
            (lows_a[j] < lvl and closes_a[j] > lvl)
            for j in range(6) for lvl in prev_lows
        )
        if swept_low and displacement_up and prev_highs and close > min(prev_highs):
            return ModuleResult(
                self.name, 1.0, 0.95,
                ["MSS: sweep лоя → импульсный displacement → слом структуры вверх"],
            )

        swept_high = any(
            (highs_a[j] > lvl and closes_a[j] < lvl)
            for j in range(6) for lvl in prev_highs
        )
        if swept_high and displacement_down and prev_lows and close < max(prev_lows):
            return ModuleResult(
                self.name, -1.0, 0.95,
                ["MSS: sweep хая → импульсный displacement → слом структуры вниз"],
            )
        return self.neutral()
