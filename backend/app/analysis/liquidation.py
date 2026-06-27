"""Liquidation Cluster Detection (оценочная модель).

Публичный API Binance не отдаёт карту ликвидаций, поэтому кластеры
оцениваются по стандартным уровням ликвидации плечевых позиций,
открытых на недавних экстремумах и зонах высокого объёма:
  для лонгов с плечом L, открытых у цены P: liq ≈ P * (1 - 1/L * 0.99)
  для шортов: liq ≈ P * (1 + 1/L * 0.99)
Плечи 10x/25x/50x/100x дают сетку магнитов под/над текущей ценой.

Сигнальная логика:
  • плотный кластер лонг-ликвидаций чуть НИЖЕ цены — магнит вниз (sweep),
    но его снятие на текущем баре (wick через кластер + возврат) — LONG;
  • симметрично для шорт-ликвидаций сверху.
"""
from __future__ import annotations

import numpy as np

from .base import AnalysisModule, MarketContext, ModuleResult, cached_atr, get_swing_points

LEVERAGES = (10, 25, 50, 100)


def estimate_clusters(ctx: MarketContext) -> dict:
    if "liq_clusters" in ctx.extras:
        return ctx.extras["liq_clusters"]

    df = ctx.df
    swings = get_swing_points(ctx)
    vol = df["volume"]
    v_thr = vol.quantile(0.8)

    # Зоны вероятного набора позиций: swing-точки + бары с высоким объёмом
    anchors: list[tuple[float, float]] = []   # (цена, вес)
    for i, p in swings["highs"][-6:] + swings["lows"][-6:]:
        anchors.append((p, float(vol.iloc[i] / vol.mean())))
    tail_close = df["close"].values[-120:]
    tail_vol = df["volume"].values[-120:]
    vmean = float(vol.mean())
    mask = tail_vol > v_thr
    for c, v in zip(tail_close[mask], tail_vol[mask]):
        anchors.append((float(c), float(v / vmean)))

    price = df["close"].iloc[-1]
    below: list[tuple[float, float]] = []   # long-liq уровни ниже цены
    above: list[tuple[float, float]] = []   # short-liq уровни выше цены
    for p, w in anchors:
        for lev in LEVERAGES:
            long_liq = p * (1 - 0.99 / lev)
            short_liq = p * (1 + 0.99 / lev)
            if long_liq < price:
                below.append((long_liq, w / np.sqrt(lev)))
            if short_liq > price:
                above.append((short_liq, w / np.sqrt(lev)))

    def cluster(levels: list[tuple[float, float]], tol: float) -> list[dict]:
        out: list[dict] = []
        for lvl, w in sorted(levels):
            if out and abs(lvl - out[-1]["price"]) <= tol:
                tot = out[-1]["weight"] + w
                out[-1]["price"] = (out[-1]["price"] * out[-1]["weight"] + lvl * w) / tot
                out[-1]["weight"] = tot
            else:
                out.append({"price": lvl, "weight": w})
        out.sort(key=lambda c: c["weight"], reverse=True)
        return out[:5]

    tol = cached_atr(ctx).iloc[-1] * 0.5
    result = {"below": cluster(below, tol), "above": cluster(above, tol)}
    ctx.extras["liq_clusters"] = result
    return result


class LiquidationClusterModule(AnalysisModule):
    name = "liquidation_clusters"
    weight = 0.9

    def analyze(self, ctx: MarketContext) -> ModuleResult:
        clusters = estimate_clusters(ctx)
        df = ctx.df
        last = df.iloc[-1]
        price = last["close"]
        a = cached_atr(ctx).iloc[-1]

        # Снятие кластера на текущем баре
        for c in clusters["below"]:
            if last["low"] <= c["price"] and price > c["price"] + 0.1 * a:
                return ModuleResult(
                    self.name, 1.0, min(1.0, 0.5 + c["weight"] * 0.1),
                    [f"Снят кластер лонг-ликвидаций ~{c['price']:.6g} — топливо собрано, возврат вверх"],
                )
        for c in clusters["above"]:
            if last["high"] >= c["price"] and price < c["price"] - 0.1 * a:
                return ModuleResult(
                    self.name, -1.0, min(1.0, 0.5 + c["weight"] * 0.1),
                    [f"Снят кластер шорт-ликвидаций ~{c['price']:.6g} — возврат вниз"],
                )

        # Близкий мощный кластер — магнит
        strongest_below = clusters["below"][0] if clusters["below"] else None
        strongest_above = clusters["above"][0] if clusters["above"] else None
        if strongest_below and (price - strongest_below["price"]) < 1.5 * a \
                and strongest_below["weight"] > (strongest_above["weight"] if strongest_above else 0) * 1.5:
            return ModuleResult(self.name, -0.5, 0.4,
                                [f"Магнит ликвидаций снизу ~{strongest_below['price']:.6g} ещё не снят"])
        if strongest_above and (strongest_above["price"] - price) < 1.5 * a \
                and strongest_above["weight"] > (strongest_below["weight"] if strongest_below else 0) * 1.5:
            return ModuleResult(self.name, 0.5, 0.4,
                                [f"Магнит ликвидаций сверху ~{strongest_above['price']:.6g} ещё не снят"])
        return self.neutral()
