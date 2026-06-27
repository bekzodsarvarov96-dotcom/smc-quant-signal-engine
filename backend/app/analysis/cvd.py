"""CVD — Cumulative Volume Delta.

Дельта бара = taker_buy − taker_sell (агрессивные покупки минус продажи).
CVD = накопленная дельта. Анализ:
  • наклон CVD подтверждает направление;
  • дивергенция цена/CVD (цена HH при CVD LH и наоборот) — ранний разворот;
  • абсорбция: цена стоит, CVD растёт/падает — лимитные игроки поглощают поток.
"""
from __future__ import annotations

import numpy as np

from .base import AnalysisModule, MarketContext, ModuleResult


class CVDModule(AnalysisModule):
    name = "cvd"
    weight = 1.2

    def analyze(self, ctx: MarketContext) -> ModuleResult:
        df = ctx.df
        if "taker_buy_base" not in df.columns or len(df) < 60:
            return self.neutral("Нет данных дельты тейкеров", no_data=True)

        delta = 2 * df["taker_buy_base"] - df["volume"]   # buy − sell
        cvd = delta.cumsum()
        ctx.extras["cvd"] = cvd

        look = 20
        price_chg = df["close"].iloc[-1] - df["close"].iloc[-look]
        cvd_chg = cvd.iloc[-1] - cvd.iloc[-look]
        vol_norm = df["volume"].iloc[-look:].sum()
        if vol_norm <= 0:
            return self.neutral()
        cvd_strength = cvd_chg / vol_norm   # -1..1 примерно

        reasons: list[str] = []

        # Дивергенции на экстремумах
        win_p = df["close"].iloc[-look:]
        win_c = cvd.iloc[-look:]
        p_hh = win_p.iloc[-1] >= win_p.max() * 0.999
        p_ll = win_p.iloc[-1] <= win_p.min() * 1.001
        c_below_peak = win_c.iloc[-1] < win_c.max() - 0.15 * vol_norm
        c_above_bottom = win_c.iloc[-1] > win_c.min() + 0.15 * vol_norm

        if p_hh and c_below_peak:
            return ModuleResult(self.name, -1.0, 0.8,
                                ["CVD-дивергенция: цена обновляет хай без поддержки дельты покупок"])
        if p_ll and c_above_bottom:
            return ModuleResult(self.name, 1.0, 0.8,
                                ["CVD-дивергенция: цена обновляет лоу, но дельта продаж иссякает"])

        # Абсорбция
        if abs(price_chg) / df["close"].iloc[-1] < 0.002 and abs(cvd_strength) > 0.12:
            if cvd_strength > 0:
                return ModuleResult(self.name, -0.6, 0.55,
                                    ["Абсорбция покупок: агрессивные покупки не двигают цену"])
            return ModuleResult(self.name, 0.6, 0.55,
                                ["Абсорбция продаж: агрессивные продажи не двигают цену"])

        # Подтверждение направления потоком
        if cvd_strength > 0.08 and price_chg > 0:
            return ModuleResult(self.name, 1.0, min(1.0, 0.4 + cvd_strength * 2),
                                [f"CVD растёт вместе с ценой — поток покупателей ({cvd_strength:+.2f})"])
        if cvd_strength < -0.08 and price_chg < 0:
            return ModuleResult(self.name, -1.0, min(1.0, 0.4 - cvd_strength * 2),
                                [f"CVD падает вместе с ценой — поток продавцов ({cvd_strength:+.2f})"])
        return self.neutral()
