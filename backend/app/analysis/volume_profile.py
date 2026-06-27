"""Volume Profile: POC, Value Area High/Low по последним N свечам.

Объём каждой свечи равномерно распределяется по её диапазону high–low
на ценовую сетку. POC — бин с максимальным объёмом; VA — 70% объёма.

Сигнальные ситуации:
  • отбой от VAL в аптренде → LONG; от VAH в даунтренде → SHORT;
  • принятие выше VAH (закрепление) → LONG-контекст; ниже VAL → SHORT.
"""
from __future__ import annotations

import numpy as np

from .base import AnalysisModule, MarketContext, ModuleResult, cached_atr

BINS = 50
LOOKBACK = 240
VALUE_AREA = 0.70


def build_profile(ctx: MarketContext) -> dict | None:
    if "volume_profile" in ctx.extras:
        return ctx.extras["volume_profile"]
    df = ctx.df.iloc[-LOOKBACK:]
    if len(df) < 50:
        return None

    lo, hi = df["low"].min(), df["high"].max()
    if hi <= lo:
        return None
    edges = np.linspace(lo, hi, BINS + 1)
    vol_bins = np.zeros(BINS)

    h, l, v = df["high"].values, df["low"].values, df["volume"].values
    for i in range(len(df)):
        lo_idx = np.searchsorted(edges, l[i], side="right") - 1
        hi_idx = np.searchsorted(edges, h[i], side="left")
        lo_idx, hi_idx = max(lo_idx, 0), min(hi_idx, BINS)
        span = max(hi_idx - lo_idx, 1)
        vol_bins[lo_idx:lo_idx + span] += v[i] / span

    poc_idx = int(vol_bins.argmax())
    total = vol_bins.sum()

    # Value Area: расширение от POC, пока не наберём 70% объёма
    inc = {poc_idx}
    acc = vol_bins[poc_idx]
    left, right = poc_idx - 1, poc_idx + 1
    while acc < VALUE_AREA * total and (left >= 0 or right < BINS):
        lv = vol_bins[left] if left >= 0 else -1
        rv = vol_bins[right] if right < BINS else -1
        if lv >= rv:
            inc.add(left); acc += lv; left -= 1
        else:
            inc.add(right); acc += rv; right += 1

    idxs = sorted(inc)
    centers = (edges[:-1] + edges[1:]) / 2
    profile = {
        "poc": float(centers[poc_idx]),
        "val": float(edges[idxs[0]]),
        "vah": float(edges[idxs[-1] + 1]),
    }
    ctx.extras["volume_profile"] = profile
    return profile


class VolumeProfileModule(AnalysisModule):
    name = "volume_profile"
    weight = 1.0

    def analyze(self, ctx: MarketContext) -> ModuleResult:
        prof = build_profile(ctx)
        if prof is None:
            return self.neutral()

        df = ctx.df
        price = df["close"].iloc[-1]
        low, high = df["low"].iloc[-1], df["high"].iloc[-1]
        tol = cached_atr(ctx).iloc[-1] * 0.4
        htf = ctx.extras.get("htf_trend", "range")

        # Отбой от границ Value Area
        if abs(low - prof["val"]) <= tol and price > prof["val"] and htf != "down":
            return ModuleResult(self.name, 1.0, 0.7,
                                [f"Отбой от VAL {prof['val']:.6g} (POC {prof['poc']:.6g})"])
        if abs(high - prof["vah"]) <= tol and price < prof["vah"] and htf != "up":
            return ModuleResult(self.name, -1.0, 0.7,
                                [f"Отбой от VAH {prof['vah']:.6g} (POC {prof['poc']:.6g})"])
        # Принятие за пределами Value Area
        if price > prof["vah"] + tol:
            return ModuleResult(self.name, 0.6, 0.5,
                                [f"Закрепление выше VAH {prof['vah']:.6g} — принятие цены вверх"])
        if price < prof["val"] - tol:
            return ModuleResult(self.name, -0.6, 0.5,
                                [f"Закрепление ниже VAL {prof['val']:.6g} — принятие цены вниз"])
        return self.neutral("Цена внутри Value Area")
