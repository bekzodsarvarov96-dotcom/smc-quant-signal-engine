"""GET /api/v1/system/status — здоровье сервиса.

Метрики процесса: psutil, если установлен; иначе fallback на /proc и resource.
"""
from __future__ import annotations

import os
import time

from fastapi import APIRouter

from ..backtest.auto_backtest import auto_backtest
from ..backtest.task_manager import task_manager
from ..config import get_settings
from ..engine.scanner import scanner

router = APIRouter(prefix="/api/v1/system")

START_TIME = time.time()
settings = get_settings()

try:
    import psutil
    _PROC = psutil.Process(os.getpid())
    _PROC.cpu_percent(interval=None)   # инициализация счётчика
    HAS_PSUTIL = True
except ImportError:                     # pragma: no cover
    HAS_PSUTIL = False


def _fallback_memory_mb() -> float:
    try:
        with open(f"/proc/{os.getpid()}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    return 0.0


@router.get("/status")
async def system_status() -> dict:
    uptime = time.time() - START_TIME
    if HAS_PSUTIL:
        mem_mb = round(_PROC.memory_info().rss / 1024 / 1024, 1)
        cpu = _PROC.cpu_percent(interval=None)
        sys_cpu = psutil.cpu_percent(interval=None)
        sys_mem = psutil.virtual_memory().percent
    else:
        mem_mb = _fallback_memory_mb()
        cpu = sys_cpu = sys_mem = None

    return {
        "scanner_running": scanner.running,
        "auto_backtest_enabled": settings.auto_backtest_on,
        "auto_backtest_running": auto_backtest.running,
        "active_tasks": {
            "backtests": task_manager.active_count,
            "details": [t.public() for t in task_manager.tasks.values()
                        if t.status in ("queued", "running")],
        },
        "memory_usage": {"process_mb": mem_mb, "system_pct": sys_mem},
        "cpu_usage": {"process_pct": cpu, "system_pct": sys_cpu},
        "uptime": {
            "seconds": round(uptime, 1),
            "human": f"{int(uptime // 3600)}h {int(uptime % 3600 // 60)}m {int(uptime % 60)}s",
        },
    }
