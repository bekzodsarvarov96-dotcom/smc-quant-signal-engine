"""Open Interest Delta: скорость и ускорение изменения OI в связке с ценой.

Дополняет базовый OI-модуль:
  • резкий рост OI delta при пробое — топливо для продолжения;
  • резкое схлопывание OI (каскад ликвидаций) после движения — признак
    кульминации, контекст разворота.
"""
from __future__ import annotations

from .base import AnalysisModule, MarketContext, ModuleResult


class OIDeltaModule(AnalysisModule):
    name = "oi_delta"
    weight = 0.9

    def analyze(self, ctx: MarketContext) -> ModuleResult:
        oi = ctx.open_interest
        if oi is None or len(oi) < 12:
            return self.neutral("Нет данных OI", no_data=True)

        s = oi["sumOpenInterest"].astype(float)
        d_fast = (s.iloc[-1] - s.iloc[-3]) / s.iloc[-3]       # короткое окно
        d_slow = (s.iloc[-1] - s.iloc[-9]) / s.iloc[-9]       # длинное окно
        accel = d_fast - d_slow / 3

        df = ctx.df
        price_chg = (df["close"].iloc[-1] - df["close"].iloc[-3]) / df["close"].iloc[-3]

        # Кульминация: сильное схлопывание OI после выраженного движения
        if d_fast < -0.02:
            if price_chg < -0.004:
                return ModuleResult(self.name, 1.0, 0.7,
                                    [f"OI delta {d_fast:+.2%}: каскад ликвидаций лонгов — кульминация падения"])
            if price_chg > 0.004:
                return ModuleResult(self.name, -1.0, 0.7,
                                    [f"OI delta {d_fast:+.2%}: ликвидация шортов — кульминация роста"])

        # Топливо: ускоряющийся приток OI по движению
        if accel > 0.008 and d_fast > 0.01:
            bias = 1.0 if price_chg > 0 else -1.0
            return ModuleResult(self.name, bias, 0.6,
                                [f"Ускоряющийся приток OI ({d_fast:+.2%}) поддерживает движение"])
        return self.neutral()
