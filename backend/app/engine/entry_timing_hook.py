"""Хук Этапа 1: вычисление метрик тайминга входа и сигнатуры сетапа.

Вызывается из scan_symbol ПОСЛЕ evaluate(). Не меняет торговую логику —
только измеряет уже принятый сигнал и формирует подпись sweep+BOS для
duplicate-фильтра Этапа 2.
"""
from __future__ import annotations

import logging

from ..analysis.entry_timing import compute_entry_timing, _find_recent_sweep
from ..analysis.base import MarketContext, get_swing_points
from ..analysis.market_structure import detect_structure

logger = logging.getLogger(__name__)


def build_timing_and_signature(ctx: MarketContext, candidate) -> tuple[dict, dict]:
    """Возвращает (entry_timing_dict, setup_signature_dict).

    setup_signature = округлённые уровни sweep и BOS — две сделки с одинаковой
    подписью построены на одном движении (для Duplicate Setup Filter).
    Любая ошибка измерения не должна влиять на публикацию — отдаём пустые dict.
    """
    try:
        m = compute_entry_timing(ctx, candidate.direction,
                                 candidate.entry, candidate.tp1)
        timing = m.as_dict()
    except Exception as exc:  # noqa: BLE001
        logger.warning("entry_timing fail %s: %s", candidate.symbol, exc)
        timing = {}

    signature: dict = {}
    try:
        st = detect_structure(ctx)
        swings = get_swing_points(ctx)
        sweep = _find_recent_sweep(ctx.df, swings, candidate.direction)
        bos_level = st["last_swing_high"] if candidate.direction == "LONG" \
            else st["last_swing_low"]
        # округляем к значащим разрядам, чтобы микроразличия не считались разными сетапами
        def rnd(x):
            if x is None:
                return None
            return float(f"{x:.5g}")
        signature = {
            "sweep": rnd(sweep[0]) if sweep else None,
            "bos": rnd(bos_level),
            "direction": candidate.direction,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("setup_signature fail %s: %s", candidate.symbol, exc)
        signature = {}

    return timing, signature
