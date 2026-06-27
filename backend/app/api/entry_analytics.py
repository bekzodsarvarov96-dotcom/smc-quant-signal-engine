"""Этап 1: эндпоинт распределений метрик тайминга входа.

Только аналитика. Считает перцентили по entry_timing завершённых сделок —
основа для будущего выбора порогов (Этап 3) ПО ПЕРЦЕНТИЛЯМ, а не из головы.
"""
from __future__ import annotations

import numpy as np
from fastapi import APIRouter
from sqlalchemy import select

from ..database import SessionLocal
from ..models import ForwardTestResult

router = APIRouter(prefix="/api/v1/entry-analytics")

METRICS = ["distance_from_sweep_atr", "distance_from_bos_atr",
           "distance_from_ema50_atr", "position_in_range_percent",
           "distance_to_tp1_percent", "bars_after_sweep", "bars_after_bos"]


def _pctiles(vals: list[float]) -> dict:
    a = np.array([v for v in vals if v is not None], dtype=float)
    if len(a) == 0:
        return {"n": 0}
    return {
        "n": int(len(a)),
        "p10": round(float(np.percentile(a, 10)), 3),
        "p25": round(float(np.percentile(a, 25)), 3),
        "p50": round(float(np.percentile(a, 50)), 3),
        "p75": round(float(np.percentile(a, 75)), 3),
        "p90": round(float(np.percentile(a, 90)), 3),
        "mean": round(float(a.mean()), 3),
        "max": round(float(a.max()), 3),
    }


@router.get("/distributions")
async def distributions():
    """Распределения метрик тайминга по завершённым сделкам + срез по исходу."""
    async with SessionLocal() as db:
        rows = (await db.execute(select(ForwardTestResult))).scalars().all()
    if not rows:
        return {"completed_trades": 0,
                "note": "Нет завершённых сделок. Метрики копятся по мере закрытия сигналов."}

    out = {"completed_trades": len(rows), "metrics": {}}
    for key in METRICS:
        vals = [(r.entry_timing or {}).get(key) for r in rows]
        out["metrics"][key] = _pctiles(vals)

    # срез: поздние ли входы у проигрышных сделок? (диагностика премисы)
    wins = [r for r in rows if r.final_r > 0]
    losses = [r for r in rows if r.final_r <= 0]
    out["by_outcome"] = {}
    for key in ("distance_from_sweep_atr", "distance_from_bos_atr",
                "position_in_range_percent"):
        out["by_outcome"][key] = {
            "wins_p50": _pctiles([(r.entry_timing or {}).get(key) for r in wins]).get("p50"),
            "losses_p50": _pctiles([(r.entry_timing or {}).get(key) for r in losses]).get("p50"),
        }
    if len(rows) < 100:
        out["warning"] = (f"Завершённых сделок {len(rows)} (<100). Пороги Этапа 3 "
                          "определять только после накопления ≥100 (план пользователя).")
    return out
