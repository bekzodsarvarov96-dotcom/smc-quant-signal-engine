"""Диагностический слой фильтров (Вариант А: ТОЛЬКО измерение).

Считает 6 факторов для каждого сигнала и фиксирует, ЧТО БЫ сделал каждый
фильтр, если бы был активен (бонус к направлению, отклонение, корректировка
score, итоговый Confidence). НИЧЕГО не применяет: score, grade, confirmations
и решение о публикации остаются нетронутыми. Цель — накопить 100–200 сделок и
на данных проверить, какие фильтры реально улучшают expectancy, прежде чем
включать что-либо.

Внешние данные (L/S ratio, Fear & Greed) из контейнера недоступны (403) —
при недоступности фактор помечается no_data и ни на что не влияет.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from ..analysis.base import MarketContext, atr

logger = logging.getLogger(__name__)

# Пороги — ИЗ ТЗ пользователя, помечены как времянка-кандидаты на замену
# перцентильными значениями после накопления статистики (НЕ применяются сейчас).
LS_CROWD_PCT = 70.0          # толпа в одну сторону
LS_BONUS = 10.0
FG_FEAR = 20.0
FG_GREED = 80.0
FG_BONUS = 10.0
ATR_EXP_MIN = 1.1            # ATR последнего бара / средний ATR
RELVOL_MIN = 1.2            # объём последнего бара / средний
OI_EXP_MIN = 1.05
HIST_MIN_TRADES = 20
CONF_BANDS = [(0, 50, "Reject"), (50, 65, "Weak"), (65, 80, "Good"), (80, 100, "Premium")]
CONF_PUBLISH_MIN = 70        # «публиковать только ≥70» — ДИАГНОСТИЧЕСКИ, не gate


def _band(value: float) -> str:
    for lo, hi, name in CONF_BANDS:
        if lo <= value < hi + (0.001 if hi == 100 else 0):
            return name
    return "Reject"


def compute_volatility_expansion(ctx: MarketContext) -> dict:
    """Фильтр 3: признаки расширения волатильности из УЖЕ имеющихся данных."""
    df = ctx.df
    out = {"atr_expansion": None, "relative_volume": None,
           "oi_expansion": None, "would_reject_flat": None}
    if df is None or len(df) < 30:
        return out
    a = atr(df, 14)
    if len(a) >= 30 and a.iloc[-30:].mean() > 0:
        out["atr_expansion"] = round(float(a.iloc[-1] / a.iloc[-30:].mean()), 3)
    vol = df["volume"]
    if len(vol) >= 30 and vol.iloc[-30:].mean() > 0:
        out["relative_volume"] = round(float(vol.iloc[-1] / vol.iloc[-30:].mean()), 3)
    oi = ctx.open_interest
    if isinstance(oi, pd.DataFrame) and not oi.empty and len(oi) >= 10:
        s = oi["sumOpenInterest"].astype(float)
        if s.iloc[-10:].mean() > 0:
            out["oi_expansion"] = round(float(s.iloc[-1] / s.iloc[-10:].mean()), 3)
    # «флэт» = ни один из доступных признаков не показывает расширения
    checks = []
    if out["atr_expansion"] is not None:
        checks.append(out["atr_expansion"] >= ATR_EXP_MIN)
    if out["relative_volume"] is not None:
        checks.append(out["relative_volume"] >= RELVOL_MIN)
    if out["oi_expansion"] is not None:
        checks.append(out["oi_expansion"] >= OI_EXP_MIN)
    if checks:
        out["would_reject_flat"] = not any(checks)
    return out


def compute_ls_ratio_factor(direction: str, ls: dict | None) -> dict:
    """Фильтр 1: контрарианский бонус по перекосу толпы (would-be)."""
    out = {"long_pct": None, "short_pct": None,
           "would_bonus_direction": None, "would_bonus_points": 0.0}
    if not ls:
        return out
    out["long_pct"], out["short_pct"] = ls.get("long_pct"), ls.get("short_pct")
    if ls.get("long_pct", 0) > LS_CROWD_PCT:        # толпа в лонге -> бонус шорту
        out["would_bonus_direction"] = "SHORT"
        out["would_bonus_points"] = LS_BONUS if direction == "SHORT" else 0.0
    elif ls.get("short_pct", 0) > LS_CROWD_PCT:     # толпа в шорте -> бонус лонгу
        out["would_bonus_direction"] = "LONG"
        out["would_bonus_points"] = LS_BONUS if direction == "LONG" else 0.0
    return out


def compute_fear_greed_factor(direction: str, fg_value: float | None) -> dict:
    """Фильтр 2: бонус по индексу страха/жадности (would-be)."""
    out = {"value": fg_value, "class": None, "would_bonus_points": 0.0}
    if fg_value is None:
        return out
    if fg_value < FG_FEAR:
        out["class"] = "Extreme Fear"
        out["would_bonus_points"] = FG_BONUS if direction == "LONG" else 0.0
    elif fg_value > FG_GREED:
        out["class"] = "Extreme Greed"
        out["would_bonus_points"] = FG_BONUS if direction == "SHORT" else 0.0
    else:
        out["class"] = "Neutral"
    return out


def compute_historical_factor(symbol_stats: dict | None) -> dict:
    """Фильтр 4: корректировка по истории пары (would-be, из forward-статистики)."""
    out = {"trades": 0, "winrate": None, "profit_factor": None,
           "expectancy": None, "would_score_adjustment": 0.0}
    if not symbol_stats or symbol_stats.get("trades", 0) < HIST_MIN_TRADES:
        if symbol_stats:
            out.update({k: symbol_stats.get(k) for k in
                        ("trades", "winrate", "profit_factor", "expectancy")})
        return out
    out.update({k: symbol_stats.get(k) for k in
                ("trades", "winrate", "profit_factor", "expectancy")})
    pf, wr = symbol_stats.get("profit_factor"), symbol_stats.get("winrate")
    if pf is not None and pf < 0.8 or (wr is not None and wr < 35):
        out["would_score_adjustment"] = -10.0
    elif pf is not None and pf > 1.3 and wr is not None and wr > 50:
        out["would_score_adjustment"] = 10.0
    return out


def adaptive_feature_snapshot(direction: str, ctx: MarketContext,
                              score: float, vol_factor: dict) -> dict:
    """Фильтр 5: снимок признаков для последующего анализа убыточных комбинаций.
    Только захват данных — никакой авто-подстройки score."""
    funding_sign = None
    if isinstance(ctx.funding, pd.DataFrame) and not ctx.funding.empty:
        funding_sign = "positive" if float(ctx.funding["fundingRate"].iloc[-1]) > 0 else "negative"
    oi_dir = None
    oi = ctx.open_interest
    if isinstance(oi, pd.DataFrame) and not oi.empty and len(oi) >= 6:
        s = oi["sumOpenInterest"].astype(float)
        oi_dir = "rising" if s.iloc[-1] > s.iloc[-6] else "falling"
    vol_state = None
    if vol_factor.get("relative_volume") is not None:
        vol_state = "high" if vol_factor["relative_volume"] >= RELVOL_MIN else "weak"
    return {"funding": funding_sign, "open_interest": oi_dir,
            "volume_state": vol_state, "score": round(score, 1)}


def compute_filter_diagnostics(direction: str, base_score: float, ctx: MarketContext,
                               ls: dict | None, fg_value: float | None,
                               symbol_stats: dict | None) -> dict:
    """Главная: собирает все 6 факторов. Возвращает диагностический dict.
    base_score — РЕАЛЬНЫЙ score из scoring (не меняется). Confidence — проекция:
    что было бы, если бы бонусы/корректировки применились (для будущего анализа)."""
    vol = compute_volatility_expansion(ctx)
    f1 = compute_ls_ratio_factor(direction, ls)
    f2 = compute_fear_greed_factor(direction, fg_value)
    f4 = compute_historical_factor(symbol_stats)
    f5 = adaptive_feature_snapshot(direction, ctx, base_score, vol)

    # Проектируемый Confidence = реальный score + сумма would-be эффектов (диагностика!)
    projected = base_score + f1["would_bonus_points"] + f2["would_bonus_points"] \
        + f4["would_score_adjustment"]
    projected = float(max(0.0, min(100.0, projected)))

    # Причины «как если бы фильтры были активны» (для лога качества фильтров)
    would_reject_reasons = []
    if vol.get("would_reject_flat"):
        would_reject_reasons.append("Low Volatility (флэт)")
    if f4["would_score_adjustment"] < 0:
        would_reject_reasons.append("Bad Historical Performance")
    if projected < CONF_PUBLISH_MIN:
        would_reject_reasons.append(f"Confidence<{CONF_PUBLISH_MIN:.0f}")

    return {
        "volatility": vol,
        "long_short_ratio": f1,
        "fear_greed": f2,
        "historical": f4,
        "adaptive_features": f5,
        "confidence": {
            "base_score": round(base_score, 1),
            "projected_confidence": round(projected, 1),
            "band": _band(projected),
            "would_publish": projected >= CONF_PUBLISH_MIN,
        },
        "would_reject": bool(would_reject_reasons),
        "would_reject_reasons": would_reject_reasons,
        "_note": "ДИАГНОСТИКА: фильтры не применены, сигнал опубликован по валидированной логике",
    }
