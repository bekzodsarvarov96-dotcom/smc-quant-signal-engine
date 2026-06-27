"""Метрики тайминга входа (Этап 1: ТОЛЬКО измерение, без фильтров).

Вычисляются из свечей + структуры рынка ПОСЛЕ того, как evaluate() уже
принял решение о сигнале. Торговую логику (score/grade/confirmations/
families/risk) НЕ меняют — это чистый аналитический слой поверх кандидата.

Для каждого сигнала измеряем «насколько поздний вход»:
  distance_from_sweep_atr  — на сколько ATR цена ушла от точки сбора ликвидности
  distance_from_bos_atr    — на сколько ATR от уровня пробоя структуры
  distance_from_ema50_atr  — на сколько ATR от EMA50 (растяжение/перекупленность)
  position_in_range_percent— позиция входа в диапазоне последнего импульса (0..100)
  distance_to_tp1_percent  — какую долю пути до TP1 цена уже прошла бы от sweep
  bars_after_sweep         — сколько баров прошло с момента sweep
  bars_after_bos           — сколько баров прошло с момента BOS

Все величины — диагностические; решений на их основе на Этапе 1 не принимается.
None означает «не удалось определить опорную точку» (например, нет sweep).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..analysis.base import MarketContext, atr, get_swing_points
from ..analysis.market_structure import detect_structure


@dataclass
class EntryTimingMetrics:
    distance_from_sweep_atr: float | None = None
    distance_from_bos_atr: float | None = None
    distance_from_ema50_atr: float | None = None
    position_in_range_percent: float | None = None
    distance_to_tp1_percent: float | None = None
    bars_after_sweep: int | None = None
    bars_after_bos: int | None = None

    def as_dict(self) -> dict:
        return {
            "distance_from_sweep_atr": self.distance_from_sweep_atr,
            "distance_from_bos_atr": self.distance_from_bos_atr,
            "distance_from_ema50_atr": self.distance_from_ema50_atr,
            "position_in_range_percent": self.position_in_range_percent,
            "distance_to_tp1_percent": self.distance_to_tp1_percent,
            "bars_after_sweep": self.bars_after_sweep,
            "bars_after_bos": self.bars_after_bos,
        }


def _find_recent_sweep(df: pd.DataFrame, swings: dict, direction: str,
                       lookback: int = 30) -> tuple[float, int] | None:
    """Ищет недавний sweep: для LONG — прокол свинг-лоя вниз с возвратом выше,
    для SHORT — прокол свинг-хая вверх с возвратом ниже. Возвращает (цена
    экстремума прокола, индекс бара) или None.
    Чистая геометрия, та же дефиниция, что использует sweep-модуль концептуально."""
    n = len(df)
    lows = df["low"].values
    highs = df["high"].values
    closes = df["close"].values
    start = max(0, n - lookback)

    if direction == "LONG":
        lvls = [(i, p) for i, p in swings["lows"] if i >= start - 5]
        for i, lvl in reversed(lvls):
            # после свинг-лоя есть бар, проколовший его и закрывшийся выше
            for j in range(i + 1, n):
                if lows[j] < lvl and closes[j] > lvl:
                    return float(lows[j]), j
    else:
        lvls = [(i, p) for i, p in swings["highs"] if i >= start - 5]
        for i, lvl in reversed(lvls):
            for j in range(i + 1, n):
                if highs[j] > lvl and closes[j] < lvl:
                    return float(highs[j]), j
    return None


def compute_entry_timing(ctx: MarketContext, direction: str,
                         entry: float, tp1: float) -> EntryTimingMetrics:
    df = ctx.df
    n = len(df)
    m = EntryTimingMetrics()
    a = float(atr(df, 14).iloc[-1])
    if a <= 0:
        return m

    swings = get_swing_points(ctx)
    st = detect_structure(ctx)

    # --- sweep ---
    sweep = _find_recent_sweep(df, swings, direction)
    if sweep is not None:
        sweep_price, sweep_idx = sweep
        if direction == "LONG":
            m.distance_from_sweep_atr = round((entry - sweep_price) / a, 3)
        else:
            m.distance_from_sweep_atr = round((sweep_price - entry) / a, 3)
        m.bars_after_sweep = int(n - 1 - sweep_idx)

    # --- BOS уровень = последний пробитый swing ---
    bos_level = st["last_swing_high"] if direction == "LONG" else st["last_swing_low"]
    if bos_level is not None:
        if direction == "LONG":
            m.distance_from_bos_atr = round((entry - bos_level) / a, 3)
        else:
            m.distance_from_bos_atr = round((bos_level - entry) / a, 3)
        # bars_after_bos: ищем последний бар, где close пересёк bos_level
        closes = df["close"].values
        for j in range(n - 1, -1, -1):
            crossed = (closes[j] > bos_level) if direction == "LONG" else (closes[j] < bos_level)
            prev = (closes[j - 1] <= bos_level) if direction == "LONG" else (closes[j - 1] >= bos_level)
            if j > 0 and crossed and prev:
                m.bars_after_bos = int(n - 1 - j)
                break

    # --- EMA50 растяжение ---
    if n >= 50:
        ema50 = df["close"].ewm(span=50, adjust=False).mean().iloc[-1]
        m.distance_from_ema50_atr = round(abs(entry - float(ema50)) / a, 3)

    # --- позиция в диапазоне последнего импульса ---
    # импульс = от последнего противоположного свинга до последнего согласного
    if direction == "LONG" and st["last_swing_low"] is not None and st["last_swing_high"] is not None:
        lo, hi = st["last_swing_low"], st["last_swing_high"]
        if hi > lo:
            m.position_in_range_percent = round((entry - lo) / (hi - lo) * 100, 1)
    elif direction == "SHORT" and st["last_swing_low"] is not None and st["last_swing_high"] is not None:
        lo, hi = st["last_swing_low"], st["last_swing_high"]
        if hi > lo:
            m.position_in_range_percent = round((hi - entry) / (hi - lo) * 100, 1)

    # --- доля пути до TP1, пройденная от sweep к моменту входа ---
    if sweep is not None and abs(tp1 - sweep[0]) > 1e-12:
        traveled = abs(entry - sweep[0])
        full = abs(tp1 - sweep[0])
        m.distance_to_tp1_percent = round(min(traveled / full * 100, 999), 1)

    return m
