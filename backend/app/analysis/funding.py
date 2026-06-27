"""Funding Rate Extremes.

Два уровня анализа:
  1) Абсолютные пороги (±0.05% / ±0.10% за 8ч).
  2) Перцентильные экстремумы: текущий funding в топ/боттом 10% собственной
     истории за последние ~10 дней — статистический экстремум, даже если
     абсолютное значение умеренное (важно для альткоинов).
Контртрендовая логика: перегретые лонги → SHORT-фактор, и наоборот.
"""
from __future__ import annotations

from .base import AnalysisModule, MarketContext, ModuleResult

EXTREME = 0.0005
STRONG = 0.001


class FundingRateModule(AnalysisModule):
    name = "funding_rate"
    weight = 0.8

    def analyze(self, ctx: MarketContext) -> ModuleResult:
        f = ctx.funding
        if f is None or f.empty:
            return self.neutral("Нет данных Funding Rate", no_data=True)

        rates = f["fundingRate"]
        rate = rates.iloc[-1]
        avg3 = rates.iloc[-3:].mean()

        pct_rank = (rates < rate).mean() if len(rates) >= 15 else 0.5

        # Абсолютные экстремумы
        if avg3 >= STRONG:
            return ModuleResult(self.name, -1.0, 0.85,
                                [f"Funding {rate:+.4%} — экстремальный перегрев лонгов"])
        if avg3 <= -STRONG:
            return ModuleResult(self.name, 1.0, 0.85,
                                [f"Funding {rate:+.4%} — экстремальный перегрев шортов (squeeze-потенциал)"])

        # Перцентильные экстремумы
        if pct_rank >= 0.9 and rate > 0:
            return ModuleResult(self.name, -0.8, 0.6,
                                [f"Funding {rate:+.4%} в топ-10% своей истории — статистический экстремум лонгов"])
        if pct_rank <= 0.1 and rate < 0:
            return ModuleResult(self.name, 0.8, 0.6,
                                [f"Funding {rate:+.4%} в нижних 10% истории — статистический экстремум шортов"])

        if avg3 >= EXTREME:
            return ModuleResult(self.name, -0.5, 0.45, [f"Funding {rate:+.4%} — умеренный перекос в лонги"])
        if avg3 <= -EXTREME:
            return ModuleResult(self.name, 0.5, 0.45, [f"Funding {rate:+.4%} — умеренный перекос в шорты"])
        return self.neutral("Funding нейтрален")
