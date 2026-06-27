"""Базовые типы модульной системы анализа.

Каждый алгоритм — отдельный класс-наследник AnalysisModule.
Модуль получает MarketContext и возвращает ModuleResult:
  bias  ∈ [-1; +1]  — направление (минус = SHORT, плюс = LONG)
  score ∈ [0; 1]    — сила/уверенность сигнала модуля
  reasons           — человекочитаемые причины (для отчёта в сигнале)

Новый алгоритм добавляется так:
  1) создать файл в app/analysis/
  2) унаследоваться от AnalysisModule, задать name и weight
  3) зарегистрировать в registry.py — всё.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class MarketContext:
    """Все данные, необходимые модулям анализа."""
    symbol: str
    timeframe: str
    df: pd.DataFrame                 # закрытые свечи LTF (open/high/low/close/volume)
    htf_df: pd.DataFrame | None = None   # старший таймфрейм для контекста тренда
    open_interest: pd.DataFrame | None = None
    funding: pd.DataFrame | None = None
    extras: dict = field(default_factory=dict)  # кэш между модулями (swings и т.п.)


@dataclass
class ModuleResult:
    name: str
    bias: float            # -1..+1
    score: float           # 0..1
    reasons: list[str] = field(default_factory=list)
    no_data: bool = False  # модулю не хватило данных (не «нейтральное мнение»)

    @property
    def directional_score(self) -> float:
        return self.bias * self.score


class AnalysisModule(ABC):
    name: str = "base"
    weight: float = 1.0    # вес модуля в итоговой оценке 0–100

    @abstractmethod
    def analyze(self, ctx: MarketContext) -> ModuleResult:
        ...

    def neutral(self, reason: str = "", no_data: bool = False) -> ModuleResult:
        return ModuleResult(self.name, 0.0, 0.0,
                            [reason] if reason else [], no_data=no_data)


# ---------- Общие утилиты ----------

def find_swings(df: pd.DataFrame, lookback: int = 3) -> tuple[pd.Series, pd.Series]:
    """Фрактальные swing high / swing low (векторизовано).

    Точка — swing high, если её high строго выше high `lookback` свечей
    слева и справа (аналогично для swing low). Возвращает булевы серии.
    """
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    k = 2 * lookback + 1
    sh = np.zeros(n, dtype=bool)
    sl = np.zeros(n, dtype=bool)
    if n >= k:
        wh = np.lib.stride_tricks.sliding_window_view(highs, k)
        wl = np.lib.stride_tricks.sliding_window_view(lows, k)
        center_h = wh[:, lookback]
        center_l = wl[:, lookback]
        is_h = (center_h == wh.max(axis=1)) & ((wh == center_h[:, None]).sum(axis=1) == 1)
        is_l = (center_l == wl.min(axis=1)) & ((wl == center_l[:, None]).sum(axis=1) == 1)
        sh[lookback: n - lookback] = is_h
        sl[lookback: n - lookback] = is_l
    return pd.Series(sh, index=df.index), pd.Series(sl, index=df.index)


def get_swing_points(ctx: MarketContext, lookback: int = 3) -> dict:
    """Кэшируемое извлечение swing-точек в ctx.extras."""
    key = f"swings_{lookback}"
    if key in ctx.extras:
        return ctx.extras[key]
    sh, sl = find_swings(ctx.df, lookback)
    hv, lv = ctx.df["high"].values, ctx.df["low"].values
    swing_highs = [(int(i), float(hv[i])) for i in np.where(sh.values)[0]]
    swing_lows = [(int(i), float(lv[i])) for i in np.where(sl.values)[0]]
    data = {"highs": swing_highs, "lows": swing_lows}
    ctx.extras[key] = data
    return data


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def cached_atr(ctx: "MarketContext", period: int = 14) -> pd.Series:
    """ATR с кэшированием в ctx.extras (модули зовут ATR по 10+ раз за бар)."""
    key = f"atr_{period}"
    if key not in ctx.extras:
        ctx.extras[key] = atr(ctx.df, period)
    return ctx.extras[key]
