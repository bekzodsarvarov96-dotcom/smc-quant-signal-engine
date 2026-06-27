"""Менеджер ручных бэктест-задач.

POST /api/v1/backtest/run создаёт asyncio-задачу и сразу возвращает task_id —
API остаётся отзывчивым, тяжёлый расчёт идёт в фоне. Статусы задач видны
в GET /api/v1/system/status и GET /api/v1/backtest/tasks.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

MAX_CONCURRENT_BACKTESTS = 1     # бэктест тяжёлый: один за раз, остальные в очереди


@dataclass
class BacktestTask:
    id: str
    symbol: str
    timeframe: str
    months: int
    status: str = "queued"            # queued | running | done | error
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    result_run_id: int | None = None  # id записи BacktestRun в БД

    def public(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


class BacktestTaskManager:
    def __init__(self) -> None:
        self.tasks: dict[str, BacktestTask] = {}
        self._sem = asyncio.Semaphore(MAX_CONCURRENT_BACKTESTS)

    @property
    def active_count(self) -> int:
        return sum(1 for t in self.tasks.values() if t.status in ("queued", "running"))

    def submit(self, symbol: str, timeframe: str, months: int) -> BacktestTask:
        task = BacktestTask(id=uuid.uuid4().hex[:12], symbol=symbol.upper(),
                            timeframe=timeframe, months=months)
        self.tasks[task.id] = task
        asyncio.create_task(self._run(task))
        return task

    async def _run(self, task: BacktestTask) -> None:
        from ..database import SessionLocal
        from ..models import BacktestRun
        from .backtester import run_backtest

        async with self._sem:                      # очередь тяжёлых задач
            task.status = "running"
            task.started_at = datetime.utcnow().isoformat()
            try:
                result = await run_backtest(task.symbol, task.timeframe,
                                            days=task.months * 30)
                async with SessionLocal() as db:
                    run = BacktestRun(**result)
                    db.add(run)
                    await db.commit()
                    await db.refresh(run)
                    task.result_run_id = run.id
                task.status = "done"
                logger.info("Бэктест-задача %s завершена: run_id=%s",
                            task.id, task.result_run_id)
            except Exception as exc:               # noqa: BLE001
                task.status = "error"
                task.error = str(exc)
                logger.exception("Бэктест-задача %s упала: %s", task.id, exc)
            finally:
                task.finished_at = datetime.utcnow().isoformat()
                # держим только последние 50 задач в памяти
                if len(self.tasks) > 50:
                    for tid in list(self.tasks)[:-50]:
                        if self.tasks[tid].status in ("done", "error"):
                            self.tasks.pop(tid, None)


task_manager = BacktestTaskManager()
