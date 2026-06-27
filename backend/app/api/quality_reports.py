"""Quality Reports (диагностика, НЕ влияет на сигналы).

7 read-only эндпоинтов поверх forward_test_results для поиска статистически
подтверждённых паттернов ПЕРЕД созданием будущего фильтрационного слоя:
  /grade-quality      — метрики по грейдам A+/A/B/C/D
  /symbol-quality     — метрики по символам + флаг underperforming
  /volume-quality     — сделки с модулем Volume vs без
  /rsi-quality        — с RSI Divergence vs без
  /oi-delta-quality   — с OI Delta vs без
  /combo-quality      — все комбинации модулей (trades>=5), по expectancy
  (blacklist встроен в symbol-quality как диагностический флаг)

ВАЖНО про статистику: на малой выборке (десятки сделок) winrate и PF имеют
широкий доверительный интервал. Каждый ответ содержит ci_low/ci_high для
winrate (Wilson, 95%) и флаг significant=False при n<MIN_SIGNIFICANT —
чтобы прибыльные на вид комбинации не приняли за доказанный edge.
"""
from __future__ import annotations

import math
from itertools import combinations

import numpy as np
from fastapi import APIRouter, Query
from sqlalchemy import select

from ..database import SessionLocal
from ..models import ForwardTestResult

router = APIRouter(prefix="/api/v1")

MIN_TRADES = 5            # минимум для показа среза
MIN_SIGNIFICANT = 30     # ниже — winrate/PF считаем статистически ненадёжными
GRADES = ["A+", "A", "B", "C", "D"]


def _wilson(wins: int, n: int) -> tuple[float, float]:
    """95% доверительный интервал доли (Wilson score). Возвращает (low%, high%)."""
    if n == 0:
        return (0.0, 0.0)
    z = 1.96
    phat = wins / n
    denom = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return (round(max(0, centre - half) * 100, 1), round(min(1, centre + half) * 100, 1))


def _metrics(rs: list[float]) -> dict:
    a = np.array(rs, dtype=float)
    n = len(a)
    if n == 0:
        return {"trades": 0, "wins": 0, "losses": 0, "winrate": None,
                "expectancy": None, "profit_factor": None}
    wins_mask = a > 0
    wins, losses = int(wins_mask.sum()), int((~wins_mask).sum())
    gw, gl = a[wins_mask].sum(), abs(a[~wins_mask].sum())
    wr = wins / n
    ci_low, ci_high = _wilson(wins, n)
    return {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "winrate": round(wr * 100, 1),
        "winrate_ci95": [ci_low, ci_high],
        "expectancy": round(float(a.mean()), 4),
        "profit_factor": round(float(gw / gl), 3) if gl > 0 else None,
        "significant": n >= MIN_SIGNIFICANT,
    }


def _mods(r: ForwardTestResult) -> set:
    return set(r.active_modules or [])


async def _load():
    async with SessionLocal() as db:
        return (await db.execute(select(ForwardTestResult))).scalars().all()


def _caveat(rows) -> dict:
    n = len(rows)
    if n >= 200:
        return {}
    if n >= MIN_SIGNIFICANT:
        return {"warning": (
            f"Завершённых сделок {n}. Общая выборка приемлема, НО срезы по грейдам/"
            "символам/комбинациям дробят её на группы по 5-10 сделок, где winrate/PF "
            "статистически ненадёжны. Ориентируйтесь на significant=True и ширину "
            "winrate_ci95, а не на точечные значения. Надёжные выводы по срезам — от ~200 сделок.")}
    return {"warning": (
        f"Завершённых сделок {n} (<{MIN_SIGNIFICANT}). Все срезы статистически "
        "ненадёжны: winrate имеет доверительный интервал ±20-40 п.п., PF чувствителен "
        "к 1-2 сделкам. Не отбирайте фильтры по этим числам — это шум, пока не накоплено "
        "100-200 сделок. Смотрите на ширину winrate_ci95, а не на точечный winrate.")}


def _split(rows, has_module: str) -> dict:
    """Сравнение 'с модулем' vs 'без модуля' + expectancy_gap."""
    with_m = [r.final_r for r in rows if has_module in _mods(r)]
    without_m = [r.final_r for r in rows if has_module not in _mods(r)]
    a, b = _metrics(with_m), _metrics(without_m)
    gap = None
    if a["expectancy"] is not None and b["expectancy"] is not None:
        gap = round(a["expectancy"] - b["expectancy"], 4)
    return {"module": has_module, "with_module": a, "without_module": b,
            "expectancy_gap": gap,
            "reading": ("Положительный gap -> сделки с модулем лучше. Но проверьте "
                        "trades и winrate_ci95 обеих групп: при перекрытии интервалов "
                        "разница не доказана.")}


@router.get("/grade-quality")
async def grade_quality():
    rows = await _load()
    if not rows:
        return {"completed_trades": 0, "note": "Нет завершённых сделок."}
    out = {g: _metrics([r.final_r for r in rows if r.grade == g]) for g in GRADES}
    return {"completed_trades": len(rows), "by_grade": out, **_caveat(rows)}


@router.get("/symbol-quality")
async def symbol_quality():
    rows = await _load()
    if not rows:
        return {"completed_trades": 0, "note": "Нет завершённых сделок."}
    syms = sorted({r.symbol for r in rows})
    items = []
    for sym in syms:
        m = _metrics([r.final_r for r in rows if r.symbol == sym])
        # Диагностический blacklist-флаг (НЕ исключает символ, только помечает)
        underperforming = (m["trades"] >= 5 and
                           ((m["winrate"] is not None and m["winrate"] < 30) or
                            (m["expectancy"] is not None and m["expectancy"] < -0.5)))
        m["symbol"] = sym
        m["underperforming"] = underperforming
        items.append(m)
    items.sort(key=lambda x: (x["expectancy"] is None, -(x["expectancy"] or 0)))
    flagged = [i["symbol"] for i in items if i["underperforming"]]
    return {"completed_trades": len(rows), "by_symbol": items,
            "underperforming_symbols": flagged,
            "note": "underperforming — только диагностический флаг, символ НЕ исключён из сканера.",
            **_caveat(rows)}


@router.get("/volume-quality")
async def volume_quality():
    rows = await _load()
    if not rows:
        return {"completed_trades": 0, "note": "Нет завершённых сделок."}
    return {"completed_trades": len(rows), **_split(rows, "volume"), **_caveat(rows)}


@router.get("/rsi-quality")
async def rsi_quality():
    rows = await _load()
    if not rows:
        return {"completed_trades": 0, "note": "Нет завершённых сделок."}
    return {"completed_trades": len(rows), **_split(rows, "rsi_divergence"), **_caveat(rows)}


@router.get("/oi-delta-quality")
async def oi_delta_quality():
    rows = await _load()
    if not rows:
        return {"completed_trades": 0, "note": "Нет завершённых сделок."}
    return {"completed_trades": len(rows), **_split(rows, "oi_delta"), **_caveat(rows)}


@router.get("/combo-quality")
async def combo_quality(min_trades: int = Query(MIN_TRADES, ge=1),
                        max_size: int = Query(3, ge=2, le=4)):
    rows = await _load()
    if not rows:
        return {"completed_trades": 0, "note": "Нет завершённых сделок."}
    combo_rs: dict[tuple, list[float]] = {}
    for r in rows:
        mods = sorted(_mods(r))
        for k in range(2, max_size + 1):
            for c in combinations(mods, k):
                combo_rs.setdefault(c, []).append(r.final_r)
    items = []
    for c, rs in combo_rs.items():
        if len(rs) < min_trades:
            continue
        m = _metrics(rs)
        m["modules"] = " + ".join(c)
        m["size"] = len(c)
        items.append(m)
    items.sort(key=lambda x: (x["expectancy"] is None, -(x["expectancy"] or 0)))
    # цель пользователя: WR>60, PF>1.5, exp>0 — но только если significant
    targets = [i for i in items if i["winrate"] and i["winrate"] > 60
               and i["profit_factor"] and i["profit_factor"] > 1.5
               and i["expectancy"] and i["expectancy"] > 0]
    return {"completed_trades": len(rows), "combinations": items,
            "meeting_target_WR60_PF1.5_posExp": targets,
            "target_note": ("Комбинации, формально проходящие цель. КРИТИЧНО: на текущей "
                            "выборке почти все они significant=False — это кандидаты в гипотезы, "
                            "а НЕ доказанные фильтры. Проверять на 100–200 сделках до внедрения."),
            **_caveat(rows)}
