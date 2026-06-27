"""Фильтр 4 (диагностика): историческая статистика пары из forward-результатов.

Считает по завершённым forward-сделкам символа: trades, winrate, profit_factor,
expectancy. Используется ТОЛЬКО для записи would-be корректировки в диагностику
сигнала — на публикацию и score не влияет.
"""
from __future__ import annotations

import numpy as np
from sqlalchemy import select

from ..models import ForwardTestResult


async def symbol_history(db, symbol: str) -> dict | None:
    rows = (await db.execute(
        select(ForwardTestResult.final_r).where(ForwardTestResult.symbol == symbol)
    )).scalars().all()
    if not rows:
        return {"trades": 0}
    a = np.array(rows, dtype=float)
    wins, losses = a[a > 0], a[a <= 0]
    gw, gl = wins.sum(), abs(losses.sum())
    return {
        "trades": int(len(a)),
        "winrate": round(float((a > 0).mean() * 100), 1),
        "profit_factor": round(float(gw / gl), 3) if gl > 0 else None,
        "expectancy": round(float(a.mean()), 4),
    }
