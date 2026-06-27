"""Endpoint предпросмотра графика сигнала (отладка без Telegram)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from sqlalchemy import select

from ..binance.client import get_client
from ..database import SessionLocal
from ..models import Signal
from ..notifications.chart import render_signal_chart, CANDLES

router = APIRouter(prefix="/api/v1")


@router.get("/chart-preview/{signal_id}")
async def chart_preview(signal_id: int):
    """PNG-график существующего сигнала. Свечи берутся из Binance вокруг времени сигнала."""
    async with SessionLocal() as db:
        sig = (await db.execute(select(Signal).where(Signal.id == signal_id))).scalars().first()
    if sig is None:
        raise HTTPException(404, f"Сигнал {signal_id} не найден")

    client = get_client()
    # запас баров до времени сигнала + немного после
    start = sig.created_at.replace(tzinfo=timezone.utc) - timedelta(
        hours=(CANDLES + 20) * (4 if sig.timeframe == "4h" else 1))
    try:
        df = await client.klines_history(sig.symbol, sig.timeframe, start)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Не удалось получить свечи: {exc}")
    if df is None or df.empty:
        raise HTTPException(502, "Нет данных свечей для графика")

    png = render_signal_chart(sig, df)
    return Response(content=png, media_type="image/png")
