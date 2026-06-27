"""WebSocket-стрим Binance Futures: live-обновление последней свечи.

Используется сканером как опциональный быстрый канал данных;
основной анализ строится на закрытых свечах через REST.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable

import websockets

logger = logging.getLogger(__name__)

WS_BASE = "wss://fstream.binance.com/stream"


class KlineStream:
    """Подписка на kline-стримы нескольких символов с авто-reconnect."""

    def __init__(self, symbols: list[str], interval: str,
                 on_closed_candle: Callable[[str, dict], None]):
        self.symbols = [s.lower() for s in symbols]
        self.interval = interval
        self.on_closed_candle = on_closed_candle
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    @property
    def url(self) -> str:
        streams = "/".join(f"{s}@kline_{self.interval}" for s in self.symbols)
        return f"{WS_BASE}?streams={streams}"

    def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()

    async def _run(self) -> None:
        backoff = 1
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.url, ping_interval=20) as ws:
                    logger.info("WS connected: %s", self.url)
                    backoff = 1
                    async for raw in ws:
                        msg = json.loads(raw)
                        k = msg.get("data", {}).get("k")
                        if k and k.get("x"):  # x=true => свеча закрыта
                            self.on_closed_candle(k["s"], k)
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("WS error: %s — reconnect in %ss", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
