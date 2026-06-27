"""Точка входа FastAPI: инициализация БД, запуск сканера, маршруты."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .api.system import router as system_router
from .api.attribution import router as attribution_router
from .api.forward import router as forward_router
from .api.entry_analytics import router as entry_analytics_router
from .api.chart import router as chart_router
from .api.trade_attribution import router as trade_attribution_router
from .api.filter_quality import router as filter_quality_router
from .api.quality_reports import router as quality_reports_router
from .database import init_db
from .config import get_settings
from .engine.scanner import scanner
from .backtest.auto_backtest import auto_backtest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    scanner.start()
    if get_settings().auto_backtest_on:      # тяжёлый бэктест — только явно
        auto_backtest.start()
    from .notifications.bot import telegram_bot
    telegram_bot.start()                     # no-op без TELEGRAM_BOT_TOKEN
    yield
    await telegram_bot.stop()
    await auto_backtest.stop()
    await scanner.stop()


app = FastAPI(
    title="Crypto Signals API",
    version="1.0.0",
    description="SMC + индикаторный анализ Binance Futures: сигналы LONG/SHORT "
                "со скорингом 0–100, бэктестингом и журналом сделок.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(system_router)
app.include_router(attribution_router)
app.include_router(forward_router)
app.include_router(entry_analytics_router)
app.include_router(chart_router)
app.include_router(trade_attribution_router)
app.include_router(filter_quality_router)
app.include_router(quality_reports_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
