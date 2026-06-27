"""Этап 2: два безопасных фильтра публикации (только ветка top20).

НЕ меняют структуру стратегии (score/grade/families/risk), только подавляют
повторную ПУБЛИКАЦИЮ уже найденного сигнала:

1. blocked_by_choch_cooldown — после сигнала symbol+direction подавляются
   новые сигналы того же направления, пока не появился противоположный CHOCH
   ИЛИ не прошло COOLDOWN_HOURS часов. Убирает серию однонаправленных
   сигналов на одном движении.

2. is_duplicate_setup — если новый сигнал построен на тех же sweep и BOS,
   что последний сигнал того же направления по символу, он не публикуется.
   Убирает каскад «тот же сетап → несколько входов».

Оба фильтра идемпотентны и read-only по торговой логике: вызываются ПОСЛЕ
evaluate(), на готовом кандидате.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from ..analysis.base import MarketContext
from ..analysis.market_structure import detect_structure
from ..models import Signal, Direction, SignalStatus

COOLDOWN_HOURS = 12
SWEEP_TOL = 0.0015   # 0.15% — уровни ближе считаются «тем же» sweep/BOS
BOS_TOL = 0.0015


async def blocked_by_choch_cooldown(db, symbol: str, direction: str,
                                    ctx: MarketContext) -> bool:
    """True -> подавить новый сигнал. Логика:
    берём последний сигнал того же symbol+direction; если он младше
    COOLDOWN_HOURS И с тех пор НЕ появился противоположный CHOCH — блок."""
    last = (await db.execute(
        select(Signal).where(
            Signal.symbol == symbol,
            Signal.direction == Direction(direction),
        ).order_by(Signal.created_at.desc()).limit(1)
    )).scalars().first()
    if last is None:
        return False

    age = datetime.utcnow() - last.created_at
    if age >= timedelta(hours=COOLDOWN_HOURS):
        return False   # время вышло — разрешаем независимо от CHOCH

    # время не вышло: разрешаем только если появился ВСТРЕЧНЫЙ CHOCH
    st = detect_structure(ctx)
    choch = st.get("choch")
    opposite_choch = (direction == "LONG" and choch == "down") or \
                     (direction == "SHORT" and choch == "up")
    return not opposite_choch   # нет встречного CHOCH и <12ч -> блок


def _close(a: float | None, b: float | None, tol: float) -> bool:
    if a is None or b is None:
        return False
    if max(abs(a), abs(b)) == 0:
        return True
    return abs(a - b) / max(abs(a), abs(b)) <= tol


async def is_duplicate_setup(db, symbol: str, direction: str,
                             signature: dict) -> bool:
    """True -> тот же sweep+BOS, что у последнего сигнала того же направления."""
    if not signature or (signature.get("sweep") is None and signature.get("bos") is None):
        return False
    last = (await db.execute(
        select(Signal).where(
            Signal.symbol == symbol,
            Signal.direction == Direction(direction),
        ).order_by(Signal.created_at.desc()).limit(1)
    )).scalars().first()
    if last is None or not last.setup_signature:
        return False
    prev = last.setup_signature

    import json

    if isinstance(prev, str):
        try:
            prev = json.loads(prev)
        except Exception:
            prev = {}

    if not isinstance(prev, dict):
        prev = {}

    same_sweep = _close(
        signature.get("sweep"),
        prev.get("sweep"),
        SWEEP_TOL,
    )

    same_bos = _close(
        signature.get("bos"),
        prev.get("bos"),
        BOS_TOL,
    )
    # дубликат, если совпали ОБА опорных уровня (или единственный доступный)
    have_both = signature.get("sweep") is not None and signature.get("bos") is not None
    if have_both:
        return same_sweep and same_bos
    return same_sweep or same_bos
