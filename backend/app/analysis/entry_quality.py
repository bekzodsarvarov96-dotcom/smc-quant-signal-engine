"""Entry Quality — диагностический слой (НЕ влияет на сигнал/score/grade/forward).

Три показателя 0..100, вычисляемые ПОСЛЕ публикации сигнала из уже
существующих величин:
  • entry_timing (distance_from_bos_atr, distance_from_ema50_atr,
    position_in_range_percent, distance_to_tp1_percent, bars_after_bos) — Этап 1;
  • module_scores (направленная сила модулей) — для Pressure.

Energy Score   — сколько «энергии» осталось в движении (выше = раньше вход).
Pressure Score — насколько сильно поток давит в сторону сигнала.
Late Risk      — насколько поздний вход (выше = позже). Главная метрика.
Verdict        — порог по Late Risk: EARLY / GOOD / LATE / VERY LATE.

Все формулы — прозрачные кусочно-линейные отображения опорных величин в 0..100.
Это эвристики для НАКОПЛЕНИЯ статистики, а не оптимизированные пороги: их
валидность проверяется на 100–200 сделках, а не постулируется.
"""
from __future__ import annotations

from dataclasses import dataclass


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _lin(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    """Линейная интерполяция x∈[x0,x1] -> [y0,y1] с насыщением на краях."""
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    t = max(0.0, min(1.0, t))
    return y0 + t * (y1 - y0)


# Группы модулей для Pressure (направленный поток в сторону сигнала)
_FLOW_PRESSURE = ("volume", "cvd", "open_interest", "oi_delta", "vwap")
_TREND_HOLD = ("ema_trend", "mtf_trend", "vwap")


@dataclass
class EntryQuality:
    energy_score: float
    pressure_score: float
    late_risk: float
    entry_verdict: str

    def as_dict(self) -> dict:
        return {
            "energy_score": round(self.energy_score, 1),
            "pressure_score": round(self.pressure_score, 1),
            "late_risk": round(self.late_risk, 1),
            "entry_verdict": self.entry_verdict,
        }


def _verdict(late_risk: float) -> str:
    if late_risk < 25:
        return "EARLY ENTRY"
    if late_risk < 50:
        return "GOOD ENTRY"
    if late_risk < 75:
        return "LATE ENTRY"
    return "VERY LATE ENTRY"


def compute_entry_quality(entry_timing: dict, module_scores: dict) -> EntryQuality:
    """entry_timing — dict метрик Этапа 1; module_scores — {module: bias*score*sign}."""
    et = entry_timing or {}
    ms = module_scores or {}

    d_bos = et.get("distance_from_bos_atr")
    d_ema = et.get("distance_from_ema50_atr")
    pos = et.get("position_in_range_percent")
    to_tp1 = et.get("distance_to_tp1_percent")
    bars_bos = et.get("bars_after_bos")

    # ---------------- LATE RISK (главная метрика) ----------------
    # Чем дальше цена ушла от структуры/EMA и чем больше пути до TP1 пройдено —
    # тем позже вход. Композит из доступных компонент (с усреднением весов).
    comps: list[tuple[float, float]] = []   # (значение 0..100, вес)
    if d_bos is not None:
        # 0 ATR от BOS -> 0 риска; >=3 ATR -> 100
        comps.append((_lin(abs(d_bos), 0.0, 3.0, 0, 100), 1.3))
    if d_ema is not None:
        # на EMA -> 0; >=2.5 ATR растяжения -> 100
        comps.append((_lin(d_ema, 0.0, 2.5, 0, 100), 1.0))
    if pos is not None:
        # позиция в импульсном диапазоне: 0% (у основания) -> ранний; 100% (у вершины) -> поздний
        comps.append((_clip(pos), 1.2))
    if to_tp1 is not None:
        # доля пути до TP1, пройденная от sweep: 0% -> рано; >=70% -> очень поздно
        comps.append((_lin(to_tp1, 0.0, 70.0, 0, 100), 1.0))
    if bars_bos is not None:
        # свежесть пробоя: 0 баров -> рано; >=20 баров -> поздно
        comps.append((_lin(bars_bos, 0.0, 20.0, 0, 100), 0.5))

    if comps:
        wsum = sum(w for _, w in comps)
        late_risk = _clip(sum(v * w for v, w in comps) / wsum)
    else:
        late_risk = 50.0   # нет данных — нейтрально

    # ---------------- ENERGY SCORE ----------------
    # «Сколько энергии осталось» ~ обратное late_risk, скорректированное на
    # растяжение (чем дальше от EMA, тем меньше энергии) — но это всё ещё
    # производная тех же величин, поэтому базируем на (100 - late_risk)
    # и слегка штрафуем сильное ATR-растяжение.
    energy = 100.0 - late_risk
    if d_ema is not None and d_ema > 2.0:
        energy *= 0.8                      # явная перекупленность/перепроданность
    energy_score = _clip(energy)

    # ---------------- PRESSURE SCORE ----------------
    # Сила направленного потока: сумма положительных вкладов flow-модулей и
    # удержания тренда (module_scores уже ориентированы в сторону сигнала: >0 = за).
    flow_push = sum(max(0.0, ms.get(k, 0.0)) for k in _FLOW_PRESSURE)
    hold_push = sum(max(0.0, ms.get(k, 0.0)) for k in _TREND_HOLD)
    # нормировка: эмпирический максимум ~ (5 модулей × ~0.6) для flow
    pressure = _lin(flow_push, 0.0, 3.0, 0, 70) + _lin(hold_push, 0.0, 2.0, 0, 30)
    pressure_score = _clip(pressure)

    return EntryQuality(energy_score, pressure_score, late_risk,
                        _verdict(late_risk))
