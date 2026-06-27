"""Фильтр 2 (диагностика): глобальный индекс страха и жадности.

Источник alternative.me/fng — суточное значение, кэшируется на 1 час.
Из контейнера 403; на машине пользователя отдаёт значение 0..100.
Недоступность -> None (фактор помечается no_data, ни на что не влияет).
"""
from __future__ import annotations

import logging
import time

import aiohttp

logger = logging.getLogger(__name__)

_cache: dict = {"value": None, "ts": 0.0}
_TTL = 3600  # суточный индекс — часа кэша достаточно


async def get_fear_greed() -> float | None:
    now = time.time()
    if _cache["value"] is not None and now - _cache["ts"] < _TTL:
        return _cache["value"]
    try:
        async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.get("https://api.alternative.me/fng/?limit=1") as r:
                data = await r.json()
                val = float(data["data"][0]["value"])
                _cache.update({"value": val, "ts": now})
                return val
    except Exception as exc:  # noqa: BLE001
        logger.debug("Fear&Greed недоступен: %s", exc)
        return None
