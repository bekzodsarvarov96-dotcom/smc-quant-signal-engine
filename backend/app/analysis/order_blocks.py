"""Order Blocks (SMC) с ранжированием силы.

Bullish OB — последняя медвежья свеча перед импульсным ростом; bearish —
зеркально. Каждому OB присваивается strength ∈ [0;1] по факторам:
  • сила импульса после OB (тело/ATR)            — 40%
  • объём свечи OB относительно среднего          — 25%
  • свежесть (моложе = сильнее)                   — 20%
  • первое касание (нетронутая зона)              — 15%
Модуль реагирует только на OB с strength >= 0.45; score масштабируется силой.
"""
from __future__ import annotations

from .base import AnalysisModule, MarketContext, ModuleResult, cached_atr


def find_order_blocks(ctx: MarketContext, impulse_atr_mult: float = 1.5,
                      max_age: int = 60) -> dict:
    if "order_blocks" in ctx.extras:
        return ctx.extras["order_blocks"]

    import numpy as np
    df = ctx.df
    n = len(df)
    o = df["open"].to_numpy()
    c = df["close"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    v = df["volume"].to_numpy()
    a = cached_atr(ctx).to_numpy()
    vol_mean = df["volume"].rolling(50, min_periods=10).mean().to_numpy()

    body_next = c[1:] - o[1:]                       # тело свечи i+1 для индекса i
    is_bear = c[:-1] < o[:-1]
    is_bull = c[:-1] > o[:-1]
    imp_up = body_next > impulse_atr_mult * a[:-1]
    imp_dn = -body_next > impulse_atr_mult * a[:-1]

    suf_min_low = np.minimum.accumulate(l[::-1])[::-1]
    suf_max_high = np.maximum.accumulate(h[::-1])[::-1]

    def make(i: int) -> dict:
        return {"idx": int(i), "low": float(l[i]), "high": float(h[i]),
                "impulse": float(abs(body_next[i]) / max(a[i], 1e-12)),
                "vol_ratio": float(v[i] / max(vol_mean[i], 1e-12)),
                "mitigated": False, "touched": 0}

    bull_idx = np.where(is_bear & imp_up)[0]
    bear_idx = np.where(is_bull & imp_dn)[0]
    bull_idx = bull_idx[(bull_idx >= 2) & (n - bull_idx <= max_age + 2)]
    bear_idx = bear_idx[(bear_idx >= 2) & (n - bear_idx <= max_age + 2)]

    bullish = [make(i) for i in bull_idx]
    bearish = [make(i) for i in bear_idx]

    for ob in bullish:
        j = ob["idx"] + 2
        if j < n:
            if suf_min_low[j] < ob["low"]:
                ob["mitigated"] = True
            seg = l[j:]
            ob["touched"] = int(((seg >= ob["low"]) & (seg <= ob["high"])).sum())
    for ob in bearish:
        j = ob["idx"] + 2
        if j < n:
            if suf_max_high[j] > ob["high"]:
                ob["mitigated"] = True
            seg = h[j:]
            ob["touched"] = int(((seg >= ob["low"]) & (seg <= ob["high"])).sum())

    def strength(ob: dict) -> float:
        s_imp = min(ob["impulse"] / 3.0, 1.0) * 0.40
        s_vol = min(ob["vol_ratio"] / 2.5, 1.0) * 0.25
        s_age = max(0.0, 1.0 - (n - ob["idx"]) / max_age) * 0.20
        s_fresh = (1.0 if ob["touched"] <= 1 else 0.4) * 0.15
        return round(s_imp + s_vol + s_age + s_fresh, 3)

    for ob in bullish + bearish:
        ob["strength"] = strength(ob)

    result = {
        "bullish": sorted(
            [ob for ob in bullish if not ob["mitigated"] and n - ob["idx"] <= max_age],
            key=lambda x: x["strength"], reverse=True),
        "bearish": sorted(
            [ob for ob in bearish if not ob["mitigated"] and n - ob["idx"] <= max_age],
            key=lambda x: x["strength"], reverse=True),
    }
    ctx.extras["order_blocks"] = result
    return result


class OrderBlockModule(AnalysisModule):
    name = "order_block"
    weight = 1.3
    MIN_STRENGTH = 0.45

    def analyze(self, ctx: MarketContext) -> ModuleResult:
        obs = find_order_blocks(ctx)
        last = ctx.df.iloc[-1]

        for ob in obs["bullish"]:
            if ob["strength"] < self.MIN_STRENGTH:
                continue
            if ob["low"] <= last["low"] <= ob["high"] or ob["low"] <= last["close"] <= ob["high"]:
                return ModuleResult(
                    self.name, 1.0, 0.5 + 0.5 * ob["strength"],
                    [f"Bullish OB [{ob['low']:.6g}–{ob['high']:.6g}], "
                     f"сила {ob['strength']:.2f} (импульс ×{ob['impulse']:.1f} ATR, "
                     f"объём ×{ob['vol_ratio']:.1f})"],
                )
        for ob in obs["bearish"]:
            if ob["strength"] < self.MIN_STRENGTH:
                continue
            if ob["low"] <= last["high"] <= ob["high"] or ob["low"] <= last["close"] <= ob["high"]:
                return ModuleResult(
                    self.name, -1.0, 0.5 + 0.5 * ob["strength"],
                    [f"Bearish OB [{ob['low']:.6g}–{ob['high']:.6g}], "
                     f"сила {ob['strength']:.2f} (импульс ×{ob['impulse']:.1f} ATR, "
                     f"объём ×{ob['vol_ratio']:.1f})"],
                )
        return self.neutral()
