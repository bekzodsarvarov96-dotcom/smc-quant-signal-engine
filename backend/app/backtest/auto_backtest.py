"""Автоматический бэктест: при старте и далее раз в N часов прогоняет
все сканируемые символы на последних 24 месяцах истории и сохраняет
результаты в БД (BacktestRun). Метрики доступны через GET /api/v1/backtest."""
from __future__ import annotations

import asyncio
import logging

from ..config import get_settings
from ..database import SessionLocal
from ..models import BacktestRun
from .backtester import run_backtest

logger = logging.getLogger(__name__)
settings = get_settings()


class AutoBacktestService:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if not settings.auto_backtest_on:
            logger.info("Автобэктест отключён (ENABLE_AUTO_BACKTEST=false) — "
                        "запуск вручную: POST /api/v1/backtest/run")
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        await asyncio.sleep(30)  # дать API подняться
        while not self._stop.is_set():
            for symbol in settings.symbols:
                try:
                    days = settings.auto_backtest_months * 30
                    logger.info("Автобэктест %s (%s, %d мес)…",
                                symbol, settings.auto_backtest_timeframe,
                                settings.auto_backtest_months)
                    result = await run_backtest(
                        symbol, settings.auto_backtest_timeframe, days)
                    async with SessionLocal() as db:
                        db.add(BacktestRun(**result))
                        await db.commit()
                    logger.info(
                        "Автобэктест %s: signals=%d WR=%.1f%% PF=%.2f EV=%.3fR",
                        symbol, result["total_signals"], result["winrate"],
                        result["profit_factor"], result["expected_value"])
                except asyncio.CancelledError:
                    return
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Автобэктест %s: %s", symbol, exc)
                await asyncio.sleep(5)
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=settings.auto_backtest_interval_hours * 3600)
            except asyncio.TimeoutError:
                pass


auto_backtest = AutoBacktestService()
