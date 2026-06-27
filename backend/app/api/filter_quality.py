"""Filter Quality Analytics (диагностика).

Главный вопрос: ПОМОГ БЫ каждый из 6 фильтров, если бы был активен?
Сравнивает фактические исходы сделок, разрезанные по would-be решениям
фильтров, накопленным в filter_diagnostics при публикации.

Например: сделки, которые volatility-фильтр ПОМЕТИЛ БЫ как флэт (would_reject)
— какой у них реальный expectancy против остальных? Если он заметно хуже,
фильтр обоснован; если нет — отклонять по нему преждевременно.

Только наблюдение. Ничего не включает автоматически.
"""
from __future__ import annotations

import numpy as np
from fastapi import APIRouter, Query
from sqlalchemy import select

from ..database import SessionLocal
from ..models import ForwardTestResult

router = APIRouter(prefix="/api/v1/filter-quality")

MIN_TRADES = 5


def _stats(rs: list[float]) -> dict:
    a = np.array(rs, dtype=float)
    if len(a) == 0:
        return {"trades": 0, "winrate": None, "expectancy": None, "profit_factor": None}
    wins, losses = a[a > 0], a[a <= 0]
    gw, gl = wins.sum(), abs(losses.sum())
    return {
        "trades": int(len(a)),
        "winrate": round(float((a > 0).mean() * 100), 1),
        "expectancy": round(float(a.mean()), 4),
        "profit_factor": round(float(gw / gl), 3) if gl > 0 else None,
    }


def _split(rows, predicate) -> dict:
    """Делит сделки на 'фильтр сработал бы' vs 'нет' и сравнивает исходы."""
    yes = [r.final_r for r in rows if predicate(r) is True]
    no = [r.final_r for r in rows if predicate(r) is False]
    out = {"flagged": _stats(yes), "not_flagged": _stats(no)}
    if out["flagged"]["expectancy"] is not None and out["not_flagged"]["expectancy"] is not None:
        out["expectancy_gap"] = round(out["flagged"]["expectancy"] - out["not_flagged"]["expectancy"], 4)
    return out


def _fd(r: ForwardTestResult) -> dict:
    return r.filter_diagnostics or {}


@router.get("")
async def filter_quality(min_trades: int = Query(MIN_TRADES, ge=1)):
    async with SessionLocal() as db:
        rows = (await db.execute(select(ForwardTestResult))).scalars().all()
    rows = [r for r in rows if _fd(r)]            # только сделки с диагностикой
    if not rows:
        return {"completed_trades_with_diagnostics": 0,
                "note": "Диагностика фильтров копится с момента её включения. "
                        "Закрытых сделок с filter_diagnostics пока нет."}

    overall = _stats([r.final_r for r in rows])

    # --- Фильтр 3: помог бы volatility-фильтр? (флэт vs не-флэт) ---
    volatility = _split(rows, lambda r: _fd(r).get("volatility", {}).get("would_reject_flat"))

    # --- Фильтр 4: история пары (would_score_adjustment < 0 = плохая история) ---
    historical = _split(rows, lambda r:
        (_fd(r).get("historical", {}).get("would_score_adjustment") or 0) < 0)

    # --- Фильтр 6: Confidence-gate (would_publish False = отклонён бы) ---
    confidence = _split(rows, lambda r:
        _fd(r).get("confidence", {}).get("would_publish") is False)

    # --- Фильтры 1-2: бонус-направление совпало с прибылью? ---
    def ls_bonus_hit(r):
        pts = _fd(r).get("long_short_ratio", {}).get("would_bonus_points") or 0
        return pts > 0 if pts is not None else None
    ls = _split(rows, lambda r: ls_bonus_hit(r) if ls_bonus_hit(r) is not None else None)

    def fg_bonus_hit(r):
        pts = _fd(r).get("fear_greed", {}).get("would_bonus_points") or 0
        return pts > 0
    fg = _split(rows, fg_bonus_hit)

    # --- Фильтр 5: убыточные комбинации признаков (funding/OI/volume) ---
    combo_rs: dict[str, list[float]] = {}
    for r in rows:
        f = _fd(r).get("adaptive_features", {})
        key = f"funding={f.get('funding')},OI={f.get('open_interest')},vol={f.get('volume_state')}"
        combo_rs.setdefault(key, []).append(r.final_r)
    adaptive = []
    for key, rs in combo_rs.items():
        if len(rs) < min_trades:
            continue
        st = _stats(rs)
        st["combo"] = key
        adaptive.append(st)
    adaptive.sort(key=lambda x: x["expectancy"])     # худшие комбинации сверху

    out = {
        "completed_trades_with_diagnostics": len(rows),
        "overall": overall,
        "filter_3_volatility": volatility,
        "filter_4_historical": historical,
        "filter_6_confidence_gate": confidence,
        "filter_1_long_short": ls,
        "filter_2_fear_greed": fg,
        "filter_5_feature_combos": adaptive,
        "interpretation": (
            "Для каждого фильтра: 'flagged' — сделки, которые фильтр пометил бы; "
            "'not_flagged' — остальные. Отрицательный expectancy_gap означает, что "
            "помеченные фильтром сделки реально хуже -> фильтр потенциально полезен. "
            "Положительный или нулевой gap -> фильтр отклонял бы прибыльные сделки."),
    }
    if len(rows) < 100:
        out["warning"] = (f"Сделок с диагностикой {len(rows)} (<100). Любой вывод "
                          "о пользе фильтров преждевременен. Пороги в фильтрах — времянка "
                          "из ТЗ, подлежат замене перцентильными после 100–200 сделок.")
    return out
