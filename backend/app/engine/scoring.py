"""Движок оценки сигналов — Statistical Truth v1.

Изменения относительно v1.0:
  1. НОРМИРОВКА ПО АКТИВНЫМ МОДУЛЯМ:
       alignment     = Σ(w·bias·score) / Σ(w·score)  по активным модулям ∈ [−1;+1]
       participation = Σ(w активных) / Σ(w всех)
       score         = |alignment| · sqrt(min(participation/0.45, 1)) · 100
     Согласованность активных модулей × сколько системы «проснулось».
     Бонусы ±4/−6 удалены как произвольные.
  2. ПОДТВЕРЖДЕНИЯ ПО СЕМЕЙСТВАМ (families.py): минимум 3 различных семейства;
     конфликтующее семейство даёт штраф ×(1 − 0.15·n_conflicts).
  3. ГРЕЙДЫ ПО ЭМПИРИЧЕСКИМ ПЕРЦЕНТИЛЯМ (calibration.json):
       A+ ≥ p97, A ≥ p90, B ≥ p75, C ≥ p50 фактического распределения score.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from ..analysis.base import MarketContext, ModuleResult, atr, get_swing_points
from ..analysis.registry import get_modules
from ..config import get_settings
from .families import family_votes, FamilyVote

settings = get_settings()

_CALIB_PATH = Path(__file__).with_name("calibration.json")


def load_grade_cutoffs() -> dict[str, float]:
    try:
        data = json.loads(_CALIB_PATH.read_text())
        return {k: float(v) for k, v in data["cutoffs"].items()}
    except Exception:  # noqa: BLE001
        return {"A+": 72.0, "A": 60.0, "B": 48.0, "C": 35.0}


GRADE_CUTOFFS = load_grade_cutoffs()


@dataclass
class SignalCandidate:
    symbol: str
    timeframe: str
    direction: str
    score: float
    grade: str
    probability: float
    confirmations: int            # число подтвердивших СЕМЕЙСТВ
    families: dict = field(default_factory=dict)   # {family: f_score}
    entry: float = 0.0
    stop_loss: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    tp3: float = 0.0
    reasons: dict = field(default_factory=dict)
    module_scores: dict = field(default_factory=dict)
    module_raw: dict = field(default_factory=dict)   # {name: [bias, score]} — для attribution
    bar_index: int = -1           # для бэктеста


def _risk_levels(ctx: MarketContext, direction: str):
    df = ctx.df
    entry = float(df["close"].iloc[-1])
    a = float(atr(df, settings.atr_period).iloc[-1])
    swings = get_swing_points(ctx)

    if direction == "LONG":
        sl_atr = entry - a * settings.atr_sl_multiplier
        lows = [p for _, p in swings["lows"][-3:] if p < entry]
        sl = min(sl_atr, (min(lows) - a * 0.25) if lows else sl_atr)
        risk = entry - sl
        tps = tuple(entry + risk * m for m in settings.tp_r_multiples)
    else:
        sl_atr = entry + a * settings.atr_sl_multiplier
        highs = [p for _, p in swings["highs"][-3:] if p > entry]
        sl = max(sl_atr, (max(highs) + a * 0.25) if highs else sl_atr)
        risk = sl - entry
        tps = tuple(entry - risk * m for m in settings.tp_r_multiples)
    return entry, float(sl), float(tps[0]), float(tps[1]), float(tps[2])


def grade_of(score: float, n_families: int, htf_aligned: bool,
             cutoffs: dict[str, float] | None = None) -> str:
    c = cutoffs or GRADE_CUTOFFS
    if score >= c["A+"] and n_families >= 4 and htf_aligned:
        return "A+"
    if score >= c["A"] and n_families >= 4:
        return "A"
    if score >= c["B"] and n_families >= 3:
        return "B"
    if score >= c["C"]:
        return "C"
    return "D"


def evaluate(ctx: MarketContext,
             min_score: float | None = None,
             min_confirmations: int | None = None,   # минимум СЕМЕЙСТВ
             allowed_grades: list[str] | None = None,
             enforce_htf: bool | None = None) -> SignalCandidate | None:
    min_score = settings.min_signal_score if min_score is None else min_score
    min_fam = settings.min_confirmations if min_confirmations is None else min_confirmations
    allowed = settings.grades if allowed_grades is None else allowed_grades
    enforce_htf = settings.block_counter_htf if enforce_htf is None else enforce_htf

    modules = get_modules()
    results: list[ModuleResult] = [m.analyze(ctx) for m in modules]
    weights = {m.name: m.weight for m in modules}
    total_w = sum(weights.values())

    active = [r for r in results if r.score > 0.05]
    if not active:
        return None

    act_mag = sum(weights[r.name] * r.score for r in active)
    directional = sum(weights[r.name] * r.bias * r.score for r in active)
    if act_mag <= 0:
        return None

    alignment = directional / act_mag                       # −1..+1
    participation = sum(weights[r.name] for r in active) / total_w
    score = abs(alignment) * math.sqrt(min(participation / 0.45, 1.0)) * 100.0

    direction = "LONG" if directional > 0 else "SHORT"
    sign = 1.0 if directional > 0 else -1.0

    votes: dict[str, FamilyVote] = family_votes(results, weights, sign)
    confirmed = [v for v in votes.values() if v.confirmed]
    conflicting = [v for v in votes.values() if v.conflicting]

    score *= max(0.0, 1.0 - 0.15 * len(conflicting))
    score = round(min(100.0, score), 1)

    htf_trend = ctx.extras.get("htf_trend", "range")
    htf_aligned = (direction == "LONG" and htf_trend == "up") or \
                  (direction == "SHORT" and htf_trend == "down")
    counter_htf = (direction == "LONG" and htf_trend == "down") or \
                  (direction == "SHORT" and htf_trend == "up")
    if enforce_htf and counter_htf:
        return None

    grade = grade_of(score, len(confirmed), htf_aligned)

    if score < min_score or len(confirmed) < min_fam:
        return None
    if allowed and grade not in allowed:
        return None

    entry, sl, tp1, tp2, tp3 = _risk_levels(ctx, direction)
    if (direction == "LONG" and sl >= entry) or (direction == "SHORT" and sl <= entry):
        return None

    reasons = {r.name: r.reasons for r in results if r.reasons and r.bias * sign > 0}
    module_scores = {r.name: round(r.bias * r.score * sign, 3) for r in results if r.score > 0}

    return SignalCandidate(
        symbol=ctx.symbol, timeframe=ctx.timeframe, direction=direction,
        score=score, grade=grade,
        probability=round(0.40 + (score / 100.0) * 0.40, 2),  # НЕ калибрована — см. отчёт
        confirmations=len(confirmed),
        families={f: v.score for f, v in votes.items()},
        entry=entry, stop_loss=sl, tp1=tp1, tp2=tp2, tp3=tp3,
        reasons=reasons, module_scores=module_scores,
        module_raw={r.name: [round(r.bias, 4), round(r.score, 4), int(r.no_data)] for r in results},
    )
