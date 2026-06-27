"""Trade Attribution Analytics (диагностика, НЕ влияет на сигналы).

Читает завершённые forward-сделки (forward_test_results) и отвечает на вопросы:
  • какие модули чаще приводят к прибыли (по-модульная статистика);
  • какие комбинации модулей прибыльнее (попарные и тройные);
  • как Entry Quality (EARLY/GOOD/LATE/VERY LATE) связан с результатом;
  • какие диапазоны Energy / Pressure дают лучший результат.

active_modules берётся из поля результата (сохраняется при закрытии из
reasons сигнала). Для исторических записей без поля — fallback на пусто.
Вся статистика — наблюдательная: цель накопить 100–200 сделок и НА ДАННЫХ
определить, что работает, а не постулировать.
"""
from __future__ import annotations

from itertools import combinations

import numpy as np
from fastapi import APIRouter, Query
from sqlalchemy import select

from ..database import SessionLocal
from ..models import ForwardTestResult

router = APIRouter(prefix="/api/v1/trade-attribution")

MIN_TRADES = 5            # ниже этого выборка не показывается как значимая
ENERGY_BINS = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]
PRESSURE_BINS = ENERGY_BINS
VERDICTS = ["EARLY ENTRY", "GOOD ENTRY", "LATE ENTRY", "VERY LATE ENTRY"]


def _stats(rs: list[float]) -> dict:
    """Базовые метрики по списку R."""
    a = np.array(rs, dtype=float)
    if len(a) == 0:
        return {"trades": 0, "winrate": None, "avg_r": None,
                "expectancy": None, "profit_factor": None}
    wins, losses = a[a > 0], a[a <= 0]
    gw, gl = wins.sum(), abs(losses.sum())
    return {
        "trades": int(len(a)),
        "wins": int((a > 0).sum()),
        "losses": int((a <= 0).sum()),
        "winrate": round(float((a > 0).mean() * 100), 1),
        "avg_r": round(float(a.mean()), 4),
        "expectancy": round(float(a.mean()), 4),
        "profit_factor": round(float(gw / gl), 3) if gl > 0 else None,
    }


def _modules_of(r: ForwardTestResult) -> list[str]:
    return list(r.active_modules or [])


@router.get("")
async def trade_attribution(min_trades: int = Query(MIN_TRADES, ge=1)):
    async with SessionLocal() as db:
        rows = (await db.execute(select(ForwardTestResult))).scalars().all()

    if not rows:
        return {"completed_trades": 0,
                "note": "Нет завершённых сделок. Атрибуция копится по мере закрытия."}

    all_r = [r.final_r for r in rows]
    overall = _stats(all_r)

    # ---------- по модулям ----------
    module_rs: dict[str, list[float]] = {}
    for r in rows:
        for m in _modules_of(r):
            module_rs.setdefault(m, []).append(r.final_r)
    by_module = []
    for m, rs in module_rs.items():
        if len(rs) < min_trades:
            continue
        st = _stats(rs)
        # contribution: насколько лучше средней сделки, когда модуль активен
        st["module"] = m
        st["lift_vs_overall"] = round(st["avg_r"] - overall["avg_r"], 4)
        by_module.append(st)
    by_module.sort(key=lambda x: x["avg_r"], reverse=True)

    # ---------- комбинации (пары и тройки) ----------
    combo_rs: dict[tuple, list[float]] = {}
    for r in rows:
        mods = sorted(set(_modules_of(r)))
        for k in (2, 3):
            for c in combinations(mods, k):
                combo_rs.setdefault(c, []).append(r.final_r)
    by_combo = []
    for c, rs in combo_rs.items():
        if len(rs) < min_trades:
            continue
        st = _stats(rs)
        st["modules"] = " + ".join(c)
        st["size"] = len(c)
        by_combo.append(st)
    by_combo.sort(key=lambda x: x["avg_r"], reverse=True)

    # ---------- Entry Quality vs result ----------
    by_verdict = {}
    for v in VERDICTS:
        rs = [r.final_r for r in rows
              if (r.entry_quality or {}).get("entry_verdict") == v]
        by_verdict[v] = _stats(rs)

    # ---------- Energy / Pressure диапазоны ----------
    def by_range(key: str, bins) -> dict:
        out = {}
        for lo, hi in bins:
            rs = [r.final_r for r in rows
                  if (r.entry_quality or {}).get(key) is not None
                  and lo <= (r.entry_quality or {}).get(key) < (hi + (0.001 if hi == 100 else 0))]
            out[f"{lo}-{hi}"] = _stats(rs)
        return out

    energy = by_range("energy_score", ENERGY_BINS)
    pressure = by_range("pressure_score", PRESSURE_BINS)

    out = {
        "completed_trades": len(rows),
        "overall": overall,
        "by_module": by_module,
        "best_combinations": by_combo[:20],
        "entry_quality_vs_result": by_verdict,
        "energy_score_ranges": energy,
        "pressure_score_ranges": pressure,
        "min_trades_filter": min_trades,
    }
    if len(rows) < 100:
        out["warning"] = (f"Завершённых сделок {len(rows)} (<100). Выводы об «эффективности» "
                          "модулей/диапазонов преждевременны — нужна выборка 100–200. "
                          "Текущие числа диагностические, не основание для фильтров.")
    return out
