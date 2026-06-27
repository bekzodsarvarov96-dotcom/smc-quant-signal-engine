"""Volume Analysis: всплеск объёма, подтверждение направления свечой,
дельта тейкеров (агрессивные покупки/продажи)."""
from __future__ import annotations

from .base import AnalysisModule, MarketContext, ModuleResult


class VolumeModule(AnalysisModule):
    name = "volume"
    weight = 1.0

    def analyze(self, ctx: MarketContext) -> ModuleResult:
        df = ctx.df
        if len(df) < 30:
            return self.neutral()

        vol = df["volume"]
        last = df.iloc[-1]
        avg = vol.iloc[-21:-1].mean()
        if avg <= 0:
            return self.neutral()
        ratio = last["volume"] / avg

        reasons, bias, score = [], 0.0, 0.0

        candle_dir = 1.0 if last["close"] > last["open"] else -1.0
        taker_buy_share = (last["taker_buy_base"] / last["volume"]
                           if "taker_buy_base" in df.columns and last["volume"] > 0 else 0.5)

        if ratio >= 2.0:
            bias = candle_dir
            score = min(1.0, 0.5 + (ratio - 2.0) * 0.15)
            reasons.append(f"Всплеск объёма ×{ratio:.1f} от среднего на "
                           f"{'бычьей' if candle_dir > 0 else 'медвежьей'} свече")
        elif ratio >= 1.5:
            bias = candle_dir
            score = 0.4
            reasons.append(f"Повышенный объём ×{ratio:.1f} подтверждает свечу")
        else:
            return self.neutral()

        if taker_buy_share >= 0.62:
            reasons.append(f"Доля агрессивных покупок {taker_buy_share:.0%} — давление покупателей")
            bias = max(bias, 0.0) + 0.3 if bias >= 0 else bias * 0.5
        elif taker_buy_share <= 0.38:
            reasons.append(f"Доля агрессивных покупок {taker_buy_share:.0%} — давление продавцов")
            bias = min(bias, 0.0) - 0.3 if bias <= 0 else bias * 0.5

        bias = max(-1.0, min(1.0, bias))
        return ModuleResult(self.name, bias, score, reasons)
