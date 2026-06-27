"""Liquidity: Equal Highs/Lows и Liquidity Sweep (Smart Money Concepts).

EQH/EQL — кластеры почти равных экстремумов = скопление стопов (ликвидность).
Sweep — прокол такого уровня хвостом свечи с закрытием обратно:
классический забор ликвидности перед движением в противоположную сторону.
"""
from __future__ import annotations

from .base import AnalysisModule, MarketContext, ModuleResult, get_swing_points, cached_atr


def _find_equal_levels(points: list[tuple[int, float]], tol: float) -> list[dict]:
    """Группирует swing-точки в кластеры равных уровней (>=2 касания)."""
    clusters: list[dict] = []
    for idx, price in points:
        placed = False
        for cl in clusters:
            if abs(price - cl["price"]) <= tol:
                cl["touches"].append(idx)
                cl["price"] = (cl["price"] * (len(cl["touches"]) - 1) + price) / len(cl["touches"])
                placed = True
                break
        if not placed:
            clusters.append({"price": price, "touches": [idx]})
    return [c for c in clusters if len(c["touches"]) >= 2]


class EqualHighsLowsModule(AnalysisModule):
    name = "equal_highs_lows"
    weight = 0.8

    def analyze(self, ctx: MarketContext) -> ModuleResult:
        df = ctx.df
        swings = get_swing_points(ctx)
        tol = cached_atr(ctx).iloc[-1] * 0.25
        close = df["close"].iloc[-1]

        eqh = _find_equal_levels(swings["highs"][-12:], tol)
        eql = _find_equal_levels(swings["lows"][-12:], tol)

        reasons, bias, score = [], 0.0, 0.0
        # Ликвидность над EQH притягивает цену вверх (магнит) — слабый LONG-фактор,
        # ликвидность под EQL — слабый SHORT-фактор. Основной сигнал даёт sweep.
        nearest_eqh = min((c for c in eqh if c["price"] > close),
                          key=lambda c: c["price"] - close, default=None)
        nearest_eql = max((c for c in eql if c["price"] < close),
                          key=lambda c: c["price"], default=None)

        if nearest_eqh and (nearest_eqh["price"] - close) / close < 0.01:
            bias += 0.5
            reasons.append(f"EQH {nearest_eqh['price']:.6g} рядом сверху — пул ликвидности (магнит)")
        if nearest_eql and (close - nearest_eql["price"]) / close < 0.01:
            bias -= 0.5
            reasons.append(f"EQL {nearest_eql['price']:.6g} рядом снизу — пул ликвидности (магнит)")

        if not reasons:
            return self.neutral()
        score = 0.5
        bias = max(-1.0, min(1.0, bias))
        ctx.extras["eqh"] = eqh
        ctx.extras["eql"] = eql
        return ModuleResult(self.name, bias, score, reasons)


class LiquiditySweepModule(AnalysisModule):
    """Liquidity Sweep Detection (расширенный).

    Ищет снятие ликвидности в окне последних 5 баров:
      • прокол swing-уровня или EQH/EQL хвостом с закрытием обратно;
      • бонус к score, если за sweep последовал displacement (импульс ≥1 ATR
        в противоположную проколу сторону) — подтверждённый разворот от пула.
    """
    name = "liquidity_sweep"
    weight = 1.5

    def analyze(self, ctx: MarketContext) -> ModuleResult:
        df = ctx.df
        swings = get_swing_points(ctx)
        if len(df) < 12:
            return self.neutral()
        a = cached_atr(ctx).iloc[-1]
        n = len(df)

        # Уровни ликвидности: swing-точки + кластеры EQH/EQL (если уже найдены)
        low_levels = [p for i, p in swings["lows"] if i < n - 6][-6:]
        high_levels = [p for i, p in swings["highs"] if i < n - 6][-6:]
        for c in ctx.extras.get("eql", []):
            low_levels.append(c["price"])
        for c in ctx.extras.get("eqh", []):
            high_levels.append(c["price"])

        close_now = df["close"].iloc[-1]

        # Окно поиска sweep: последние 5 баров
        for j in range(n - 5, n):
            bar = df.iloc[j]
            for lvl in low_levels:
                if bar["low"] < lvl and bar["close"] > lvl:
                    score = 0.75
                    reasons = [f"Sweep лоя {lvl:.6g} (бар -{n - 1 - j}): забор sell-side ликвидности"]
                    move_after = close_now - bar["close"]
                    if move_after > a:
                        score = 0.95
                        reasons.append("Displacement вверх после sweep — разворот подтверждён")
                    if close_now > lvl:
                        return ModuleResult(self.name, 1.0, score, reasons)
            for lvl in high_levels:
                if bar["high"] > lvl and bar["close"] < lvl:
                    score = 0.75
                    reasons = [f"Sweep хая {lvl:.6g} (бар -{n - 1 - j}): забор buy-side ликвидности"]
                    move_after = bar["close"] - close_now
                    if move_after > a:
                        score = 0.95
                        reasons.append("Displacement вниз после sweep — разворот подтверждён")
                    if close_now < lvl:
                        return ModuleResult(self.name, -1.0, score, reasons)
        return self.neutral()
