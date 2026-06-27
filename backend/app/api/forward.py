"""Forward-test API: статистика журнала и сравнение с последним бэктестом."""
from __future__ import annotations

import numpy as np
from fastapi import APIRouter, Query
from sqlalchemy import select, func

from ..database import SessionLocal
from ..models import Signal, ForwardTestResult, BacktestRun

router = APIRouter(prefix="/api/v1/forward-test")


def _metrics(rs: list[float]) -> dict:
    if not rs:
        return {"n": 0, "winrate": None, "expectancy_r": None,
                "profit_factor": None, "avg_r": None,
                "avg_win_r": None, "avg_loss_r": None}
    a = np.array(rs)
    wins, losses = a[a > 0], a[a <= 0]
    gw, gl = wins.sum(), abs(losses.sum())
    return {
        "n": int(len(a)),
        "winrate": round(float((a > 0).mean() * 100), 2),
        "expectancy_r": round(float(a.mean()), 4),
        "profit_factor": round(float(gw / gl), 3) if gl > 0 else None,
        "avg_r": round(float(a.mean()), 4),
        "avg_win_r": round(float(wins.mean()), 3) if len(wins) else None,
        "avg_loss_r": round(float(losses.mean()), 3) if len(losses) else None,
    }


@router.get("/stats")
async def forward_stats():
    async with SessionLocal() as db:
        n_signals = (await db.execute(
            select(func.count()).select_from(Signal))).scalar() or 0
        results = (await db.execute(select(ForwardTestResult))).scalars().all()

    rs = [r.final_r for r in results]
    by_symbol: dict[str, dict] = {}
    for r in results:
        d = by_symbol.setdefault(r.symbol, {"trades": 0, "wins": 0, "sum_r": 0.0})
        d["trades"] += 1
        d["wins"] += 1 if r.final_r > 0 else 0
        d["sum_r"] = round(d["sum_r"] + r.final_r, 3)

    out = {
        "signals_total": n_signals,
        "completed_trades": len(results),
        "open_signals": n_signals - len(results),
        **_metrics(rs),
        "results_breakdown": {
            "WIN": sum(1 for r in results if r.result == "WIN"),
            "LOSS": sum(1 for r in results if r.result == "LOSS"),
            "BREAKEVEN": sum(1 for r in results if r.result == "BREAKEVEN"),
        },
        "avg_holding_hours": round(float(np.mean(
            [r.holding_minutes for r in results])) / 60, 1) if results else None,
        "by_symbol": by_symbol,
        "total_fees_r": round(sum(r.fees_r for r in results), 3),
        "total_funding_r": round(sum(r.funding_r for r in results), 3),
    }
    if len(results) < 30:
        out["warning"] = (f"Завершённых сделок {len(results)} (n<30): метрики "
                          "статистически незначимы, выводы преждевременны.")
    return out


@router.get("/report")
async def forward_vs_backtest(min_n: int = Query(1, ge=1)):
    """Сравнение Forward Test vs последний Backtest по WR/PF/Expectancy/AvgR.

    Backtest-база: для каждого символа из forward-результатов берётся
    САМЫЙ СВЕЖИЙ BacktestRun этого символа; метрики агрегируются по их сделкам.
    """
    async with SessionLocal() as db:
        results = (await db.execute(select(ForwardTestResult))).scalars().all()
        symbols = sorted({r.symbol for r in results}) or None

        bt_trades_r: list[float] = []
        bt_runs_used: list[dict] = []
        if symbols:
            for sym in symbols:
                run = (await db.execute(
                    select(BacktestRun).where(BacktestRun.symbol == sym)
                    .order_by(BacktestRun.created_at.desc()).limit(1)
                )).scalars().first()
                if run and run.trades:
                    rs = [t.get("pnl_r") for t in run.trades
                          if isinstance(t, dict) and t.get("pnl_r") is not None]
                    bt_trades_r += rs
                    bt_runs_used.append({"symbol": sym, "run_id": run.id,
                                         "created_at": str(run.created_at),
                                         "trades": len(rs)})

    fw = _metrics([r.final_r for r in results])
    bt = _metrics(bt_trades_r)

    def delta(k):
        if fw[k] is None or bt[k] is None:
            return None
        return round(fw[k] - bt[k], 4)

    out = {
        "forward": fw,
        "backtest": bt,
        "backtest_runs_used": bt_runs_used,
        "delta": {k: delta(k) for k in ("winrate", "profit_factor",
                                        "expectancy_r", "avg_r")},
        "notes": [
            "Forward-исходы считаются той же математикой выходов и издержек, что и бэктест.",
            "Бэктест-метрики — по сделкам последнего BacktestRun каждого символа "
            "(запустите POST /api/v1/backtest/run для актуализации).",
        ],
    }
    if fw["n"] < 30:
        out["verdict"] = "INSUFFICIENT_DATA"
        out["notes"].append(f"Forward-сделок {fw['n']} (n<30) — сравнение информативно "
                            "только после ~30+ завершённых сделок.")
    elif bt["n"] == 0:
        out["verdict"] = "NO_BACKTEST_BASE"
    else:
        exp_gap = abs((fw["expectancy_r"] or 0) - (bt["expectancy_r"] or 0))
        out["verdict"] = "CONSISTENT" if exp_gap <= 0.10 else "DIVERGENT"
        out["notes"].append("Порог согласованности: |Δexpectancy| ≤ 0.10R "
                            "(грубый, до накопления 100+ сделок).")
    return out
