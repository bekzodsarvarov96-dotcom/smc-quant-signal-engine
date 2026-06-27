"""EMA 50 / EMA 200: трендовый фильтр + динамическая поддержка/сопротивление."""
from __future__ import annotations

from .base import AnalysisModule, MarketContext, ModuleResult, cached_atr


class EMAModule(AnalysisModule):
    name = "ema_trend"
    weight = 1.2

    def analyze(self, ctx: MarketContext) -> ModuleResult:
        df = ctx.df
        if len(df) < 210:
            return self.neutral("Недостаточно свечей для EMA200")

        ema50 = df["close"].ewm(span=50, adjust=False).mean()
        ema200 = df["close"].ewm(span=200, adjust=False).mean()
        price = df["close"].iloc[-1]
        e50, e200 = ema50.iloc[-1], ema200.iloc[-1]
        tol = cached_atr(ctx).iloc[-1] * 0.5

        reasons, bias, score = [], 0.0, 0.0

        if e50 > e200 and price > e200:
            bias, score = 1.0, 0.6
            reasons.append(f"EMA50 ({e50:.6g}) > EMA200 ({e200:.6g}) — бычий тренд")
            if abs(price - e50) <= tol:
                score = 0.8
                reasons.append("Цена тестирует EMA50 как динамическую поддержку")
        elif e50 < e200 and price < e200:
            bias, score = -1.0, 0.6
            reasons.append(f"EMA50 ({e50:.6g}) < EMA200 ({e200:.6g}) — медвежий тренд")
            if abs(price - e50) <= tol:
                score = 0.8
                reasons.append("Цена тестирует EMA50 как динамическое сопротивление")
        else:
            return self.neutral("Цена между EMA50/EMA200 — тренд не подтверждён")

        # HTF-подтверждение
        if ctx.htf_df is not None and len(ctx.htf_df) >= 210:
            h50 = ctx.htf_df["close"].ewm(span=50, adjust=False).mean().iloc[-1]
            h200 = ctx.htf_df["close"].ewm(span=200, adjust=False).mean().iloc[-1]
            htf_bias = 1.0 if h50 > h200 else -1.0
            if htf_bias == bias:
                score = min(1.0, score + 0.2)
                reasons.append(f"Старший ТФ ({ctx.timeframe}→HTF) подтверждает направление")
            else:
                score *= 0.5
                reasons.append("Старший ТФ против — сила сигнала снижена")

        return ModuleResult(self.name, bias, score, reasons)
