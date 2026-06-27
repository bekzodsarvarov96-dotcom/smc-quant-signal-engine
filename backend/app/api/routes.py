"""REST API: сигналы, журнал сделок, статистика, бэктест, live-анализ."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..analysis.registry import get_modules
from ..backtest.backtester import run_backtest
from ..database import get_db
from ..engine.scanner import build_context
from ..engine.scoring import evaluate
from ..models import Signal, Trade, BacktestRun, Direction
from ..backtest.task_manager import task_manager
from ..schemas import (
    SignalOut, TradeCreate, TradeClose, TradeOut, StatsOut,
    BacktestRequest, BacktestOut, BacktestRunRequest,
)

router = APIRouter(prefix="/api/v1")


# ---------- Сигналы ----------

@router.get("/signals", response_model=list[SignalOut])
async def list_signals(
    db: AsyncSession = Depends(get_db),
    symbol: str | None = None,
    status: str | None = None,
    grade: str | None = None,
    limit: int = Query(50, le=200),
):
    stmt = select(Signal).order_by(Signal.created_at.desc()).limit(limit)
    if symbol:
        stmt = stmt.where(Signal.symbol == symbol.upper())
    if status:
        stmt = stmt.where(Signal.status == status)
    if grade:
        stmt = stmt.where(Signal.grade == grade)
    rows = (await db.execute(stmt)).scalars().all()
    return rows


@router.get("/signals/{signal_id}", response_model=SignalOut)
async def get_signal(signal_id: int, db: AsyncSession = Depends(get_db)):
    sig = await db.get(Signal, signal_id)
    if not sig:
        raise HTTPException(404, "Сигнал не найден")
    return sig


@router.get("/analyze/{symbol}")
async def analyze_now(symbol: str):
    """Live-анализ символа по запросу (без записи в БД)."""
    ctx = await build_context(symbol.upper())
    cand = evaluate(ctx, min_score=0, min_confirmations=0,
                    allowed_grades=["A+", "A", "B", "C", "D"], enforce_htf=False)
    if cand is None:
        return {"symbol": symbol.upper(), "signal": None,
                "message": "Конфлюэнции недостаточно для сигнала"}
    return {"symbol": symbol.upper(), "signal": cand.__dict__}


@router.get("/modules")
async def list_modules():
    return [{"name": m.name, "weight": m.weight} for m in get_modules()]


# ---------- Журнал сделок ----------

@router.post("/trades", response_model=TradeOut)
async def create_trade(payload: TradeCreate, db: AsyncSession = Depends(get_db)):
    trade = Trade(
        signal_id=payload.signal_id,
        symbol=payload.symbol.upper(),
        direction=Direction(payload.direction),
        entry_price=payload.entry_price,
        stop_loss=payload.stop_loss,
        take_profit=payload.take_profit,
        qty=payload.qty,
        notes=payload.notes,
    )
    db.add(trade)
    await db.commit()
    await db.refresh(trade)
    return trade


@router.get("/trades", response_model=list[TradeOut])
async def list_trades(
    db: AsyncSession = Depends(get_db),
    is_open: bool | None = None,
    limit: int = Query(100, le=500),
):
    stmt = select(Trade).order_by(Trade.opened_at.desc()).limit(limit)
    if is_open is not None:
        stmt = stmt.where(Trade.is_open == is_open)
    return (await db.execute(stmt)).scalars().all()


@router.post("/trades/{trade_id}/close", response_model=TradeOut)
async def close_trade(trade_id: int, payload: TradeClose,
                      db: AsyncSession = Depends(get_db)):
    trade = await db.get(Trade, trade_id)
    if not trade:
        raise HTTPException(404, "Сделка не найдена")
    if not trade.is_open:
        raise HTTPException(400, "Сделка уже закрыта")

    trade.exit_price = payload.exit_price
    trade.closed_at = datetime.utcnow()
    trade.is_open = False
    if payload.notes:
        trade.notes = (trade.notes or "") + f"\n{payload.notes}"

    sign = 1.0 if trade.direction == Direction.LONG else -1.0
    trade.pnl = sign * (payload.exit_price - trade.entry_price) * (trade.qty or 1.0)
    risk = abs(trade.entry_price - trade.stop_loss)
    trade.pnl_r = (sign * (payload.exit_price - trade.entry_price) / risk
                   if risk > 0 else None)

    await db.commit()
    await db.refresh(trade)
    return trade


@router.get("/stats", response_model=StatsOut)
async def stats(db: AsyncSession = Depends(get_db)):
    closed = (await db.execute(
        select(Trade).where(Trade.is_open == False)  # noqa: E712
    )).scalars().all()
    open_count = (await db.execute(
        select(func.count()).select_from(Trade).where(Trade.is_open == True)  # noqa: E712
    )).scalar() or 0

    wins = [t for t in closed if (t.pnl_r or 0) > 0]
    losses = [t for t in closed if (t.pnl_r or 0) <= 0]
    rs = [t.pnl_r for t in closed if t.pnl_r is not None]
    gross_win = sum(r for r in rs if r > 0)
    gross_loss = abs(sum(r for r in rs if r < 0))

    return StatsOut(
        total_trades=len(closed) + open_count,
        open_trades=open_count,
        closed_trades=len(closed),
        wins=len(wins),
        losses=len(losses),
        winrate=round(len(wins) / len(closed) * 100, 2) if closed else 0.0,
        total_pnl=round(sum(t.pnl or 0 for t in closed), 4),
        total_r=round(sum(rs), 2) if rs else 0.0,
        avg_r=round(sum(rs) / len(rs), 3) if rs else 0.0,
        best_r=round(max(rs), 2) if rs else 0.0,
        worst_r=round(min(rs), 2) if rs else 0.0,
        profit_factor=round(gross_win / gross_loss, 2) if gross_loss > 0 else 0.0,
    )


# ---------- Бэктест ----------

@router.post("/backtest", response_model=BacktestOut)
async def backtest(payload: BacktestRequest, db: AsyncSession = Depends(get_db)):
    if payload.days > 750:
        raise HTTPException(400, "Максимум 750 дней (~24 месяца) за один запуск")
    result = await run_backtest(
        payload.symbol.upper(), payload.timeframe, payload.days,
        min_score=payload.min_score,
        min_confirmations=payload.min_confirmations,
        allowed_grades=payload.allowed_grades,
    )
    run = BacktestRun(**result)
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


@router.post("/backtest/run", status_code=202)
async def backtest_run_async(payload: BacktestRunRequest):
    """Ручной запуск тяжёлого бэктеста В ФОНЕ. Сразу возвращает task_id;
    API остаётся отзывчивым. Прогресс — GET /api/v1/backtest/tasks
    или GET /api/v1/system/status."""
    if payload.months < 1 or payload.months > 24:
        raise HTTPException(400, "months: от 1 до 24")
    if payload.timeframe not in ("5m", "15m", "30m", "1h", "4h"):
        raise HTTPException(400, "timeframe: 5m/15m/30m/1h/4h")
    task = task_manager.submit(payload.symbol, payload.timeframe, payload.months)
    return {"task_id": task.id, "status": task.status,
            "queue_position": task_manager.active_count}


@router.get("/backtest/tasks")
async def backtest_tasks():
    return [t.public() for t in task_manager.tasks.values()]


@router.get("/backtest", response_model=list[BacktestOut])
async def backtest_history(db: AsyncSession = Depends(get_db),
                           limit: int = Query(20, le=100)):
    stmt = select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(limit)
    return (await db.execute(stmt)).scalars().all()
