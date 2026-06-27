"""Fair Value Gap с ранжированием.

Каждому FVG присваивается rank ∈ [0;1]:
  • размер гэпа относительно ATR        — 40%
  • свежесть                            — 25%
  • незаполненность (доля гэпа целая)   — 20%
  • объём импульсной свечи              — 15%
Реагируем только на гэпы с rank >= 0.4; score масштабируется рангом.
"""
from __future__ import annotations

from .base import AnalysisModule, MarketContext, ModuleResult, cached_atr


def find_fvgs(ctx: MarketContext, max_age: int = 80) -> dict:
    if "fvgs" in ctx.extras:
        return ctx.extras["fvgs"]

    df = ctx.df
    n = len(df)
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    v = df["volume"].to_numpy()
    a = cached_atr(ctx).to_numpy()
    vol_mean = df["volume"].rolling(50, min_periods=10).mean().to_numpy()

    import numpy as np
    # суффиксные экстремумы для оценки заполнения: suf_min_low[i] = min(l[i:])
    suf_min_low = np.minimum.accumulate(l[::-1])[::-1]
    suf_max_high = np.maximum.accumulate(h[::-1])[::-1]

    idx = np.arange(2, n)
    bull_mask = l[2:] > h[:-2]
    bear_mask = h[2:] < l[:-2]

    bullish, bearish = [], []
    for i in idx[bull_mask]:
        top, bottom = float(l[i]), float(h[i - 2])
        pen = max(0.0, top - float(suf_min_low[i + 1])) if i + 1 < n else 0.0
        bullish.append({"idx": int(i), "top": top, "bottom": bottom,
                        "vol_ratio": float(v[i - 1] / max(vol_mean[i - 1], 1e-12)),
                        "filled_part": min(1.0, pen / (top - bottom))})
    for i in idx[bear_mask]:
        top, bottom = float(l[i - 2]), float(h[i])
        pen = max(0.0, float(suf_max_high[i + 1]) - bottom) if i + 1 < n else 0.0
        bearish.append({"idx": int(i), "top": top, "bottom": bottom,
                        "vol_ratio": float(v[i - 1] / max(vol_mean[i - 1], 1e-12)),
                        "filled_part": min(1.0, pen / (top - bottom))})

    def rank(g: dict) -> float:
        size = (g["top"] - g["bottom"]) / max(a[min(g["idx"], n - 1)], 1e-12)
        r_size = min(size / 1.5, 1.0) * 0.40
        r_age = max(0.0, 1.0 - (n - g["idx"]) / max_age) * 0.25
        r_fill = (1.0 - g["filled_part"]) * 0.20
        r_vol = min(g["vol_ratio"] / 2.5, 1.0) * 0.15
        return round(r_size + r_age + r_fill + r_vol, 3)

    for g in bullish + bearish:
        g["rank"] = rank(g)

    result = {
        "bullish": sorted(
            [g for g in bullish if g["filled_part"] < 0.99 and n - g["idx"] <= max_age],
            key=lambda g: g["rank"], reverse=True),
        "bearish": sorted(
            [g for g in bearish if g["filled_part"] < 0.99 and n - g["idx"] <= max_age],
            key=lambda g: g["rank"], reverse=True),
    }
    ctx.extras["fvgs"] = result
    return result


class FVGModule(AnalysisModule):
    name = "fvg"
    weight = 1.1
    MIN_RANK = 0.4

    def analyze(self, ctx: MarketContext) -> ModuleResult:
        gaps = find_fvgs(ctx)
        last = ctx.df.iloc[-1]
        price = last["close"]

        for g in gaps["bullish"]:
            if g["rank"] < self.MIN_RANK:
                continue
            if g["bottom"] <= price <= g["top"] or g["bottom"] <= last["low"] <= g["top"]:
                return ModuleResult(
                    self.name, 1.0, 0.45 + 0.55 * g["rank"],
                    [f"Bullish FVG [{g['bottom']:.6g}–{g['top']:.6g}], "
                     f"ранг {g['rank']:.2f} (заполнен на {g['filled_part']:.0%})"],
                )
        for g in gaps["bearish"]:
            if g["rank"] < self.MIN_RANK:
                continue
            if g["bottom"] <= price <= g["top"] or g["bottom"] <= last["high"] <= g["top"]:
                return ModuleResult(
                    self.name, -1.0, 0.45 + 0.55 * g["rank"],
                    [f"Bearish FVG [{g['bottom']:.6g}–{g['top']:.6g}], "
                     f"ранг {g['rank']:.2f} (заполнен на {g['filled_part']:.0%})"],
                )
        return self.neutral()
