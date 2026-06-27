"""Фоновый сканер: периодический анализ символов, запись сигналов в БД,
отправка Telegram-уведомлений, трекинг статуса активных сигналов."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from ..analysis.base import MarketContext
from ..binance.client import get_client
from ..config import get_settings
from ..database import SessionLocal
from ..models import Signal, SignalStatus, Direction
from ..notifications.telegram import send_signal_alert, send_status_alert
from .scoring import evaluate


def _entry_quality_fields(timing: dict, module_scores: dict) -> dict:
    """ДИАГНОСТИКА: считает Entry Quality для записи в Signal.
    Не влияет на публикацию — поля чисто аналитические."""
    try:
        from ..analysis.entry_quality import compute_entry_quality
        eq = compute_entry_quality(timing, module_scores)
        return {"energy_score": eq.energy_score, "pressure_score": eq.pressure_score,
                "late_risk": eq.late_risk, "entry_verdict": eq.entry_verdict}
    except Exception:  # noqa: BLE001
        return {"energy_score": 0.0, "pressure_score": 0.0,
                "late_risk": 0.0, "entry_verdict": ""}

logger = logging.getLogger(__name__)
settings = get_settings()

COOLDOWN = timedelta(hours=2)   # не дублировать сигнал по символу/направлению


async def build_context(symbol: str) -> MarketContext:
    client = get_client()
    df, htf, oi, funding = await asyncio.gather(
        client.klines(symbol, settings.scan_timeframe, limit=400),
        client.klines(symbol, settings.htf_timeframe, limit=300),
        client.open_interest_hist(symbol, period=settings.scan_timeframe),
        client.funding_rate(symbol),
        return_exceptions=True,
    )
    if isinstance(df, Exception):
        raise df
    return MarketContext(
        symbol=symbol,
        timeframe=settings.scan_timeframe,
        df=df.iloc[:-1],  # анализ только по закрытым свечам
        htf_df=None if isinstance(htf, Exception) else htf.iloc[:-1],
        open_interest=None if isinstance(oi, Exception) else oi,
        funding=None if isinstance(funding, Exception) else funding,
    )


async def scan_symbol(symbol: str) -> None:
    try:
        ctx = await build_context(symbol)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось получить данные %s: %s", symbol, exc)
        return

    candidate = evaluate(ctx)
    if candidate is None:
        return

    # --- Этап 1: метрики тайминга входа (ТОЛЬКО измерение, не влияет на решение) ---
    from .entry_timing_hook import build_timing_and_signature
    timing, signature = build_timing_and_signature(ctx, candidate)

    async with SessionLocal() as db:
        # Анти-дубликат: активный сигнал того же направления за период cooldown
        stmt = select(Signal).where(
            Signal.symbol == symbol,
            Signal.direction == Direction(candidate.direction),
            Signal.status == SignalStatus.ACTIVE,
            Signal.created_at >= datetime.utcnow() - COOLDOWN,
        )
        if (await db.execute(stmt)).scalars().first():
            return

        # --- Этап 2, фильтр 1: cooldown по symbol+direction до 12ч ИЛИ противоположного CHOCH ---
        # (изменение логики публикации — только в ветке top20)
        from .stage2_filters import blocked_by_choch_cooldown, is_duplicate_setup
        if await blocked_by_choch_cooldown(db, symbol, candidate.direction, ctx):
            logger.debug("Stage2 cooldown: %s %s подавлен (нет встречного CHOCH, <12ч)",
                        symbol, candidate.direction)
            return

        # --- Этап 2, фильтр 2: тот же sweep+BOS, что у предыдущего сигнала -> не публиковать ---
        if await is_duplicate_setup(db, symbol, candidate.direction, signature):
            logger.debug("Stage2 duplicate-setup: %s %s подавлен (тот же sweep+BOS)",
                        symbol, candidate.direction)
            return

        sig = Signal(
            symbol=candidate.symbol, timeframe=candidate.timeframe,
            direction=Direction(candidate.direction),
            score=candidate.score, grade=candidate.grade,
            probability=candidate.probability,
            confirmations=candidate.confirmations,
            entry=candidate.entry, stop_loss=candidate.stop_loss,
            tp1=candidate.tp1, tp2=candidate.tp2, tp3=candidate.tp3,
            reasons=candidate.reasons, module_scores=candidate.module_scores,
            entry_timing=timing, setup_signature=signature,
            **_entry_quality_fields(timing, candidate.module_scores),
        )
        db.add(sig)
        await db.commit()
        await db.refresh(sig)
        logger.info("Сигнал %s %s grade=%s score=%.1f", symbol, candidate.direction, candidate.grade, candidate.score)

        # --- ДИАГНОСТИКА 6 фильтров (Вариант А): считается ПОСЛЕ публикации,
        #     не влияет на score/grade/решение. Любая ошибка/недоступность данных
        #     не трогает уже опубликованный сигнал. ---
        try:
            from ..analysis.filter_diagnostics import compute_filter_diagnostics
            from ..analysis.fear_greed import get_fear_greed
            from ..analysis.historical_stats import symbol_history
            ls = await _safe_ls(symbol)
            fg = await get_fear_greed()
            hist = await symbol_history(db, symbol)
            diag = compute_filter_diagnostics(candidate.direction, candidate.score,
                                              ctx, ls, fg, hist)
            sig.filter_diagnostics = diag
            await db.commit()
            if diag["would_reject"]:
                logger.info("Фильтры (диагностика) #%d: WOULD-REJECT %s",
                            sig.id, ", ".join(diag["would_reject_reasons"]))
        except Exception as exc:  # noqa: BLE001 — диагностика не влияет на сигнал
            logger.warning("Диагностика фильтров %s: %s", symbol, exc)

        # Визуализация: сначала PNG-график, затем текст (только уведомления)
        try:
            from ..notifications.chart import render_signal_chart
            from ..notifications.telegram import send_photo
            ctx_info = {
                "trend": ctx.extras.get("structure", {}).get("trend"),
                "htf_trend": ctx.extras.get("htf_trend"),
            }
            png = render_signal_chart(sig, ctx.df, ctx_info)
            await send_photo(png, caption=f"<code>#{sig.id}</code> <b>{sig.symbol} {candidate.direction}</b> · "
                                          f"score {sig.score:.1f} · grade {sig.grade}")
        except Exception as exc:  # noqa: BLE001 — график не должен влиять на сигнал
            logger.warning("Не удалось построить/отправить график %s: %s", symbol, exc)

        sent = await send_signal_alert(sig)
        if sent:
            sig.telegram_sent = True
            await db.commit()


async def track_active_signals() -> None:
    """Обновление статуса активных сигналов по текущей цене (TP/SL)."""
    client = get_client()
    async with SessionLocal() as db:
        stmt = select(Signal).where(Signal.status.in_(
            [SignalStatus.ACTIVE, SignalStatus.TP1, SignalStatus.TP2]
        ))
        signals = (await db.execute(stmt)).scalars().all()
        for sig in signals:
            try:
                price = await client.mark_price(sig.symbol)
            except Exception:  # noqa: BLE001
                continue

            new_status = None
            if sig.direction == Direction.LONG:
                if price <= sig.stop_loss:
                    new_status = SignalStatus.STOPPED
                elif price >= sig.tp3:
                    new_status = SignalStatus.TP3
                elif price >= sig.tp2 and sig.status in (SignalStatus.ACTIVE, SignalStatus.TP1):
                    new_status = SignalStatus.TP2
                elif price >= sig.tp1 and sig.status == SignalStatus.ACTIVE:
                    new_status = SignalStatus.TP1
            else:
                if price >= sig.stop_loss:
                    new_status = SignalStatus.STOPPED
                elif price <= sig.tp3:
                    new_status = SignalStatus.TP3
                elif price <= sig.tp2 and sig.status in (SignalStatus.ACTIVE, SignalStatus.TP1):
                    new_status = SignalStatus.TP2
                elif price <= sig.tp1 and sig.status == SignalStatus.ACTIVE:
                    new_status = SignalStatus.TP1

            # Экспирация старых активных сигналов
            if new_status is None and sig.status == SignalStatus.ACTIVE \
                    and sig.created_at < datetime.utcnow() - timedelta(hours=48):
                new_status = SignalStatus.EXPIRED

            if new_status and new_status != sig.status:
                sig.status = new_status
                await db.commit()
                await send_status_alert(sig, price)


class ScannerService:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
        await get_client().close()

    async def _loop(self) -> None:
        logger.info("Сканер запущен: %s (%s)", settings.symbols, settings.scan_timeframe)
        while not self._stop.is_set():
            try:
                for symbol in settings.symbols:
                    await scan_symbol(symbol)
                    await asyncio.sleep(0.5)
                from ..forward.tracker import track_signals
                await track_signals()
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                logger.exception("Ошибка цикла сканера: %s", exc)
            await asyncio.sleep(settings.scan_interval_seconds)


scanner = ScannerService()


async def _safe_ls(symbol: str):
    """L/S ratio с защитой: недоступность не влияет на сигнал (диагностика)."""
    try:
        return await get_client().global_long_short_ratio(symbol, settings.scan_timeframe)
    except Exception:  # noqa: BLE001
        return None
