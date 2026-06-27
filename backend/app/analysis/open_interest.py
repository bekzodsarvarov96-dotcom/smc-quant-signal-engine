"""Open Interest Analysis.

Классическая матрица:
  Цена ↑ + OI ↑ → новые лонги (сильный бычий сигнал)
  Цена ↓ + OI ↑ → новые шорты (сильный медвежий сигнал)
  Цена ↑ + OI ↓ → закрытие шортов (short squeeze, слабее)
  Цена ↓ + OI ↓ → закрытие лонгов (long liquidation, слабее)
"""
from __future__ import annotations

from .base import AnalysisModule, MarketContext, ModuleResult


class OpenInterestModule(AnalysisModule):
    name = "open_interest"
    weight = 1.0

    def analyze(self, ctx: MarketContext) -> ModuleResult:
        oi = ctx.open_interest
        if oi is None or len(oi) < 6:
            return self.neutral("Нет данных Open Interest", no_data=True)

        oi_now = oi["sumOpenInterest"].iloc[-1]
        oi_prev = oi["sumOpenInterest"].iloc[-5]
        if oi_prev <= 0:
            return self.neutral()
        oi_chg = (oi_now - oi_prev) / oi_prev

        df = ctx.df
        price_chg = (df["close"].iloc[-1] - df["close"].iloc[-5]) / df["close"].iloc[-5]

        if abs(oi_chg) < 0.005:
            return self.neutral("OI без значимых изменений")

        if price_chg > 0 and oi_chg > 0:
            return ModuleResult(self.name, 1.0, min(1.0, 0.5 + oi_chg * 20),
                                [f"Цена ↑ и OI ↑ ({oi_chg:+.2%}) — открываются новые лонги"])
        if price_chg < 0 and oi_chg > 0:
            return ModuleResult(self.name, -1.0, min(1.0, 0.5 + oi_chg * 20),
                                [f"Цена ↓ и OI ↑ ({oi_chg:+.2%}) — открываются новые шорты"])
        if price_chg > 0 and oi_chg < 0:
            return ModuleResult(self.name, 0.4, 0.35,
                                [f"Цена ↑ при OI ↓ ({oi_chg:+.2%}) — закрытие шортов (squeeze, слабее)"])
        return ModuleResult(self.name, -0.4, 0.35,
                            [f"Цена ↓ при OI ↓ ({oi_chg:+.2%}) — ликвидация лонгов (слабее)"])
