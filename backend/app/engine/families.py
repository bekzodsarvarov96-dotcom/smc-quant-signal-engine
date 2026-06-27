"""Семейства факторов для системы подтверждений.

Вместо подсчёта модулей считаем подтверждения от НЕЗАВИСИМЫХ семейств:
  trend     — направление рынка (MTF, EMA, структура, BOS/CHOCH/MSS)
  liquidity — зоны и заборы ликвидности (Sweep, EQH/EQL, OB, FVG, Fib, VP)
  flow      — поток ордеров и позиционирование (Volume, CVD, OI, VWAP)
  sentiment — перекос толпы (Funding, Liquidation clusters)
  momentum  — импульс (RSI divergence)

Семейство подтверждает сигнал, если его взвешенный направленный балл
F > +0.15 И хотя бы один модуль семейства активен (|bias|>0.3, score>0.3).
Семейство конфликтует, если F < −0.15.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..analysis.base import ModuleResult

FAMILY_MAP: dict[str, str] = {
    # trend
    "mtf_trend": "trend",
    "ema_trend": "trend",
    "market_structure": "trend",
    "bos": "trend",
    "choch": "trend",
    "mss": "trend",
    # liquidity
    "liquidity_sweep": "liquidity",
    "equal_highs_lows": "liquidity",
    "order_block": "liquidity",
    "fvg": "liquidity",
    "fibonacci": "liquidity",
    "volume_profile": "liquidity",
    # flow
    "volume": "flow",
    "cvd": "flow",
    "open_interest": "flow",
    "oi_delta": "flow",
    "vwap": "flow",
    # sentiment
    "funding_rate": "sentiment",
    "liquidation_clusters": "sentiment",
    # momentum
    "rsi_divergence": "momentum",
}

FAMILIES = ("trend", "liquidity", "flow", "sentiment", "momentum")


@dataclass
class FamilyVote:
    name: str
    score: float          # взвешенный направленный балл семейства, −1..+1
    confirmed: bool       # подтверждает сторону сигнала
    conflicting: bool     # активно против


def family_votes(results: list[ModuleResult], weights: dict[str, float],
                 sign: float) -> dict[str, FamilyVote]:
    """sign = +1 для LONG-кандидата, −1 для SHORT."""
    votes: dict[str, FamilyVote] = {}
    for fam in FAMILIES:
        members = [r for r in results if FAMILY_MAP.get(r.name) == fam]
        # Модули без данных (no_data) НЕ входят в знаменатель: отсутствие
        # данных — не «нейтральное мнение» и не должно гасить голос семейства.
        voting = [r for r in members if not r.no_data]
        if not voting:
            votes[fam] = FamilyVote(name=fam, score=0.0,
                                    confirmed=False, conflicting=False)
            continue
        wsum = sum(weights[r.name] for r in voting) or 1.0
        f_score = sum(r.bias * r.score * weights[r.name] for r in voting) / wsum
        f_dir = f_score * sign
        has_active = any(r.bias * sign > 0.3 and r.score > 0.3 for r in voting)
        votes[fam] = FamilyVote(
            name=fam,
            score=round(f_score, 3),
            confirmed=f_dir > 0.15 and has_active,
            conflicting=f_dir < -0.15,
        )
    return votes
