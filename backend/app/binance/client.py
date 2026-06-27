"""Клиент Binance USDT-M Futures (публичные эндпоинты, ключи не нужны).

REST: klines, mark price, open interest history, funding rate.
WebSocket-стрим — в streams.py.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import aiohttp
import pandas as pd

from ..config import get_settings

settings = get_settings()

KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]

_INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
    "6h": 21_600_000, "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000,
}


class BinanceFuturesClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or settings.binance_base_url
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get(self, path: str, params: dict | None = None) -> dict | list:
        session = await self._get_session()
        url = f"{self.base_url}{path}"
        for attempt in range(3):
            try:
                async with session.get(url, params=params) as resp:
                    if resp.status == 429:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    resp.raise_for_status()
                    return await resp.json()
            except aiohttp.ClientError:
                if attempt == 2:
                    raise
                await asyncio.sleep(1 + attempt)
        raise RuntimeError(f"Binance request failed: {path}")

    # ---------- Klines ----------

    async def klines(
        self, symbol: str, interval: str, limit: int = 500,
        start_time: int | None = None, end_time: int | None = None,
    ) -> pd.DataFrame:
        params: dict = {"symbol": symbol, "interval": interval, "limit": min(limit, 1500)}
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        raw = await self._get("/fapi/v1/klines", params)
        df = pd.DataFrame(raw, columns=KLINE_COLUMNS)
        for col in ("open", "high", "low", "close", "volume", "quote_volume",
                    "taker_buy_base", "taker_buy_quote"):
            df[col] = df[col].astype(float)
        df["trades"] = df["trades"].astype(int)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
        return df.drop(columns=["ignore"])

    async def klines_history(
        self, symbol: str, interval: str,
        start: datetime, end: datetime | None = None,
    ) -> pd.DataFrame:
        """Постраничная загрузка истории для бэктеста."""
        end = end or datetime.now(timezone.utc)
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        step = _INTERVAL_MS[interval] * 1500

        chunks: list[pd.DataFrame] = []
        cursor = start_ms
        while cursor < end_ms:
            df = await self.klines(
                symbol, interval, limit=1500,
                start_time=cursor, end_time=min(cursor + step, end_ms),
            )
            if df.empty:
                break
            chunks.append(df)
            cursor = int(df["close_time"].iloc[-1].timestamp() * 1000) + 1
            await asyncio.sleep(0.25)  # бережём rate limit
        if not chunks:
            return pd.DataFrame(columns=KLINE_COLUMNS[:-1])
        out = pd.concat(chunks, ignore_index=True)
        return out.drop_duplicates(subset="open_time").reset_index(drop=True)

    # ---------- Деривативные данные ----------

    async def open_interest_hist(
        self, symbol: str, period: str = "15m", limit: int = 96
    ) -> pd.DataFrame:
        raw = await self._get(
            "/futures/data/openInterestHist",
            {"symbol": symbol, "period": period, "limit": min(limit, 500)},
        )
        df = pd.DataFrame(raw)
        if df.empty:
            return df
        df["sumOpenInterest"] = df["sumOpenInterest"].astype(float)
        df["sumOpenInterestValue"] = df["sumOpenInterestValue"].astype(float)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df

    async def funding_rate(self, symbol: str, limit: int = 30) -> pd.DataFrame:
        raw = await self._get(
            "/fapi/v1/fundingRate", {"symbol": symbol, "limit": min(limit, 1000)}
        )
        df = pd.DataFrame(raw)
        if df.empty:
            return df
        df["fundingRate"] = df["fundingRate"].astype(float)
        df["fundingTime"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
        return df

    async def funding_history_paginated(self, symbol: str, start) -> pd.DataFrame:
        """История funding c пагинацией (для бэктеста на месяцы/годы)."""
        start_ms = int(start.timestamp() * 1000)
        chunks = []
        cursor = start_ms
        while True:
            raw = await self._get("/fapi/v1/fundingRate",
                                  {"symbol": symbol, "startTime": cursor, "limit": 1000})
            if not raw:
                break
            df = pd.DataFrame(raw)
            df["fundingRate"] = df["fundingRate"].astype(float)
            df["fundingTime"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
            chunks.append(df)
            if len(raw) < 1000:
                break
            cursor = int(raw[-1]["fundingTime"]) + 1
            await asyncio.sleep(0.25)
        if not chunks:
            return pd.DataFrame()
        return pd.concat(chunks, ignore_index=True).drop_duplicates("fundingTime")

    async def funding_rate_history(self, symbol: str,
                                   start: datetime, end: datetime | None = None) -> pd.DataFrame:
        """Полная история funding c пагинацией (1000 записей × 8ч ≈ 333 дня за запрос)."""
        end = end or datetime.now(timezone.utc)
        cursor = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        chunks: list[pd.DataFrame] = []
        while cursor < end_ms:
            raw = await self._get("/fapi/v1/fundingRate",
                                  {"symbol": symbol, "startTime": cursor,
                                   "endTime": end_ms, "limit": 1000})
            df = pd.DataFrame(raw)
            if df.empty:
                break
            df["fundingRate"] = df["fundingRate"].astype(float)
            df["fundingTime"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
            chunks.append(df)
            cursor = int(df["fundingTime"].iloc[-1].timestamp() * 1000) + 1
            await asyncio.sleep(0.2)
        if not chunks:
            return pd.DataFrame(columns=["fundingRate", "fundingTime"])
        out = pd.concat(chunks, ignore_index=True)
        return out.drop_duplicates(subset="fundingTime").reset_index(drop=True)

    async def mark_price(self, symbol: str) -> float:
        data = await self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
        return float(data["markPrice"])


_client: BinanceFuturesClient | None = None


def get_client() -> BinanceFuturesClient:
    global _client
    if _client is None:
        _client = BinanceFuturesClient()
    return _client


    async def global_long_short_ratio(self, symbol: str, period: str = "1h"):
        """Фильтр 1 (диагностика): Long/Short Account Ratio Binance Futures.
        Возвращает {long_pct, short_pct} в процентах или None при недоступности.
        Из контейнера эндпоинт 403 — работает на машине пользователя."""
        try:
            data = await self._get("/futures/data/globalLongShortAccountRatio",
                                   {"symbol": symbol, "period": period, "limit": 1})
            if not data:
                return None
            row = data[-1] if isinstance(data, list) else data
            long_acc = float(row["longAccount"])
            short_acc = float(row["shortAccount"])
            return {"long_pct": round(long_acc * 100, 1),
                    "short_pct": round(short_acc * 100, 1)}
        except Exception as exc:  # noqa: BLE001 — фактор опционален
            logger.debug("L/S ratio %s недоступен: %s", symbol, exc)
            return None
