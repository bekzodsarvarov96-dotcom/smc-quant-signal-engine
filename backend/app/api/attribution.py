"""Endpoints Attribution Analysis."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from ..backtest import attribution as attr
from ..backtest.portfolio import CostModel
from ..config import get_settings
from ..database import SessionLocal
from ..models import BacktestRun

router = APIRouter(prefix="/api/v1")
settings = get_settings()


@router.get("/module-performance")
async def module_performance(symbol: str | None = None,
                             runs: int = Query(5, le=50)):
    """Статистика каждого модуля по сделкам последних бэктестов.

    total_signals / winrate / average_r / expectancy / profit_factor считаются
    по сделкам, где модуль был активен «за» сигнал; contribution_score —
    разница expectancy против сделок без этого модуля (R/сделка).
    Источник: поле modules в сделках BacktestRun (runs, где оно есть).
    """
    async with SessionLocal() as db:
        stmt = select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(runs)
        if symbol:
            stmt = stmt.where(BacktestRun.symbol == symbol.upper())
        rows = (await db.execute(stmt)).scalars().all()

    trades = [t for r in rows for t in (r.trades or []) if isinstance(t, dict) and t.get("modules")]
    if not trades:
        raise HTTPException(
            404, "Нет сделок с атрибуцией модулей. Запустите бэктест: "
                 "POST /api/v1/backtest/run — старые прогоны атрибуции не содержат.")
    return {
        "trades_analyzed": len(trades),
        "source_runs": [r.id for r in rows],
        "modules": attr.module_performance(trades),
        "note": "contribution_score = expectancy(сделки с модулем) − expectancy(без), R/сделка",
    }


@router.get("/module-combinations")
async def module_combinations(symbol: str = "BTCUSDT",
                              timeframe: str = "1h",
                              greedy: bool = True):
    """Leave-one-out по всем модулям + жадный поиск подмножества с максимальным
    Expectancy/PF. Выбор подмножества — на In-Sample (70%), итоговая оценка —
    на Out-of-Sample (30%). Полный перебор 2^20 комбинаций невозможен,
    leave-one-out + greedy — стандартная честная аппроксимация.

    Требует кэшированный датасет: сначала POST /api/v1/backtest/run.
    """
    key = (symbol.upper(), timeframe)
    ds = attr.DATASET_CACHE.get(key)
    if ds is None:
        raise HTTPException(
            409, f"Датасет {key} не загружен. Сначала выполните "
                 f"POST /api/v1/backtest/run для {symbol}/{timeframe} — "
                 f"кандидаты закэшируются автоматически.")

    costs = CostModel(funding_series=ds["funding"])
    ms, mf = float(settings.min_signal_score), int(settings.min_confirmations)

    loo = attr.leave_one_out(ds["df"], ds["candidates"], costs, ms, mf,
                             base_disabled=settings.disabled_module_set)
    result = {
        "symbol": symbol.upper(), "timeframe": timeframe,
        "thresholds": {"min_score": ms, "min_families": mf},
        "currently_disabled": sorted(settings.disabled_module_set),
        "baseline_is": loo["baseline"]["is"],
        "baseline_oos": loo["baseline"]["oos"],
        "leave_one_out": loo["modules"],
        "summary": {
            "improve_pf": [r["module"] for r in loo["modules"] if r["verdict"] == "improves_pf"],
            "worsen_pf": [r["module"] for r in loo["modules"] if r["verdict"] == "worsens_pf"],
            "statistically_useless": [r["module"] for r in loo["modules"]
                                      if r["verdict"] == "statistically_useless"],
            "insufficient_data": [r["module"] for r in loo["modules"]
                                  if r["verdict"] == "insufficient_data"],
        },
    }
    if greedy:
        result["greedy_optimization"] = attr.greedy_backward(
            ds["df"], ds["candidates"], costs, ms, mf)
    return result
