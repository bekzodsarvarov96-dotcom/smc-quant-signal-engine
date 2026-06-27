"""RSI Divergence.

Bullish: цена делает LL, RSI — HL (медвежий импульс выдыхается).
Bearish: цена делает HH, RSI — LH.
Поиск по последним swing-точкам цены и значениям RSI в этих же барах.
"""
from __future__ import annotations

from .base import AnalysisModule, MarketContext, ModuleResult, get_swing_points, rsi


class RSIDivergenceModule(AnalysisModule):
    name = "rsi_divergence"
    weight = 1.2

    def analyze(self, ctx: MarketContext) -> ModuleResult:
        df = ctx.df
        if len(df) < 50:
            return self.neutral()

        r = rsi(df["close"])
        swings = get_swing_points(ctx)
        reasons = []

        lows = swings["lows"][-3:]
        if len(lows) >= 2:
            (i1, p1), (i2, p2) = lows[-2], lows[-1]
            if p2 < p1 and r.iloc[i2] > r.iloc[i1] and r.iloc[i2] < 45:
                reasons.append(
                    f"Bullish RSI-дивергенция: цена LL ({p1:.6g}→{p2:.6g}), "
                    f"RSI HL ({r.iloc[i1]:.1f}→{r.iloc[i2]:.1f})"
                )
                return ModuleResult(self.name, 1.0, 0.85, reasons)

        highs = swings["highs"][-3:]
        if len(highs) >= 2:
            (i1, p1), (i2, p2) = highs[-2], highs[-1]
            if p2 > p1 and r.iloc[i2] < r.iloc[i1] and r.iloc[i2] > 55:
                reasons.append(
                    f"Bearish RSI-дивергенция: цена HH ({p1:.6g}→{p2:.6g}), "
                    f"RSI LH ({r.iloc[i1]:.1f}→{r.iloc[i2]:.1f})"
                )
                return ModuleResult(self.name, -1.0, 0.85, reasons)

        return self.neutral()
