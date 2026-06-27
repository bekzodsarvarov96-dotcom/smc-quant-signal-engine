"""Telegram-бот: команды /status, /stats, /signals, /report.

Архитектура: long-polling getUpdates (отдельная asyncio-задача в lifespan),
без вебхуков и внешних зависимостей — только aiohttp. Источники данных —
существующие таблицы signals / forward_test_results и уже реализованные
обработчики API (system_status, forward_stats, forward_vs_backtest):
бот = тонкий слой форматирования, никакой собственной логики/БД.

Безопасность: команды обрабатываются ТОЛЬКО из чата TELEGRAM_CHAT_ID.
Если TELEGRAM_CHAT_ID пуст, бот отвечает на /start подсказкой с chat_id
(для первичной настройки) и игнорирует остальное.

Отказоустойчивость: любая ошибка сети/Telegram — лог + пауза, цикл живёт;
бот никак не влияет на сканер и трекер.
"""
from __future__ import annotations

import asyncio
import logging

import aiohttp
from sqlalchemy import select, func

from ..config import get_settings
from ..database import SessionLocal
from ..models import Signal, SignalStatus, ForwardTestResult

logger = logging.getLogger(__name__)
settings = get_settings()

API = "https://api.telegram.org/bot{token}/{method}"
POLL_TIMEOUT = 25          # long-poll, сек
ERROR_BACKOFF = 10


class TelegramBot:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._offset = 0

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if not settings.telegram_bot_token:
            logger.info("Telegram-бот: токен не задан — бот не запускается")
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="telegram-bot")
            logger.info("Telegram-бот запущен (long-polling)")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ------------------------------------------------------------ transport
    async def _api(self, session: aiohttp.ClientSession, method: str,
                   **params) -> dict | None:
        url = API.format(token=settings.telegram_bot_token, method=method)
        try:
            async with session.post(url, json=params,
                                    timeout=aiohttp.ClientTimeout(total=POLL_TIMEOUT + 10)) as r:
                data = await r.json()
                if not data.get("ok"):
                    logger.warning("Telegram %s: %s", method, data)
                    return None
                return data.get("result")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Telegram %s error: %s", method, exc)
            return None

    async def _reply(self, session: aiohttp.ClientSession, chat_id: int | str,
                     text: str) -> None:
        await self._api(session, "sendMessage", chat_id=chat_id, text=text,
                        parse_mode="HTML", disable_web_page_preview=True)

    # ------------------------------------------------------------ main loop
    async def _loop(self) -> None:
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    updates = await self._api(
                        session, "getUpdates",
                        offset=self._offset, timeout=POLL_TIMEOUT,
                        allowed_updates=["message"])
                    if updates is None:
                        await asyncio.sleep(ERROR_BACKOFF)
                        continue
                    for upd in updates:
                        self._offset = max(self._offset, upd["update_id"] + 1)
                        await self._handle(session, upd)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Telegram-бот цикл: %s", exc)
                    await asyncio.sleep(ERROR_BACKOFF)

    # ------------------------------------------------------------ dispatch
    async def _handle(self, session: aiohttp.ClientSession, upd: dict) -> None:
        msg = upd.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat_id = (msg.get("chat") or {}).get("id")
        if not text or chat_id is None:
            return

        configured = str(settings.telegram_chat_id).strip()
        if not configured:
            if text.startswith(("/start", "/id")):
                await self._reply(session, chat_id,
                    f"👋 Бот crypto_signals.\nВаш chat_id: <code>{chat_id}</code>\n"
                    f"Добавьте в .env: <code>TELEGRAM_CHAT_ID={chat_id}</code> "
                    f"и перезапустите сервер.")
            return
        if str(chat_id) != configured:
            return                                   # чужой чат — молча игнор

        cmd = text.split()[0].split("@")[0].lower()
        try:
            if cmd in ("/start", "/help"):
                await self._reply(session, chat_id,
                    "Команды:\n/status — состояние системы\n/stats — forward-test метрики\n"
                    "/signals — активные сигналы\n/report — forward vs backtest")
            elif cmd == "/status":
                await self._reply(session, chat_id, await self._cmd_status())
            elif cmd == "/stats":
                await self._reply(session, chat_id, await self._cmd_stats())
            elif cmd == "/signals":
                await self._reply(session, chat_id, await self._cmd_signals())
            elif cmd == "/report":
                await self._reply(session, chat_id, await self._cmd_report())
        except Exception as exc:  # noqa: BLE001
            logger.warning("Команда %s: %s", cmd, exc)
            await self._reply(session, chat_id, f"⚠️ Ошибка выполнения {cmd}: {exc}")

    # ------------------------------------------------------------ commands
    async def _cmd_status(self) -> str:
        from ..api.system import system_status
        st = await system_status()
        async with SessionLocal() as db:
            active = (await db.execute(select(func.count()).select_from(Signal)
                .where(Signal.status.in_([SignalStatus.ACTIVE, SignalStatus.TP1,
                                          SignalStatus.TP2])))).scalar() or 0
            closed = (await db.execute(select(func.count())
                .select_from(ForwardTestResult))).scalar() or 0
        return ("🖥 <b>Статус системы</b>\n\n"
                f"Сканер: {'🟢 работает' if st['scanner_running'] else '🔴 остановлен'}\n"
                f"Активных сигналов: <b>{active}</b>\n"
                f"Завершённых сделок: <b>{closed}</b>\n"
                f"Uptime: {st['uptime'].get('human', st['uptime'])}")

    async def _cmd_stats(self) -> str:
        from ..api.forward import forward_stats
        import html

        s = await forward_stats()

        logger.warning("FORWARD_STATS=%s", s)

        if s["completed_trades"] == 0:
            return (
                "📊 <b>Forward-test</b>\n\n"
                f"Сигналов: {s['signals_total']}\n"
                "Завершённых сделок пока нет."
            )

        pf = s["profit_factor"]
        warning = html.escape(str(s.get("warning", "")))

        return (
            "📊 <b>Forward-test</b>\n\n"
            f"Завершённых сделок: <b>{s['completed_trades']}</b>\n"
            f"Winrate: <b>{s['winrate']}%</b>\n"
            f"Expectancy: <b>{s['expectancy_r']:+.4f}R</b>\n"
            f"Profit Factor: <b>{pf if pf is not None else '—'}</b>\n"
            f"Средний R: <b>{s['avg_r']:+.4f}</b>\n"
            + (f"\n⚠️ {warning}" if warning else "")
        )

    async def _cmd_signals(self) -> str:
        async with SessionLocal() as db:
            rows = (await db.execute(select(Signal)
                .where(Signal.status.in_([SignalStatus.ACTIVE, SignalStatus.TP1,
                                          SignalStatus.TP2]))
                .order_by(Signal.created_at.desc()))).scalars().all()
        if not rows:
            return "📭 Активных сигналов нет."
        LIMIT = 15                       # лимит сообщения Telegram — 4096 символов
        lines = [f"📌 <b>Активные сигналы ({len(rows)})</b>"]
        for s in rows[:LIMIT]:
            mark = {"ACTIVE": "•", "TP1_HIT": "①", "TP2_HIT": "②"}.get(s.status.value, "•")
            arrow = "🟢" if s.direction.value == "LONG" else "🔴"
            lines.append(
                f"\n{mark} {arrow} <b>{s.symbol} {s.direction.value}</b> — "
                f"score {s.score:.1f}, grade {s.grade}\n"
                f"   Вход <code>{s.entry:.6g}</code> | SL <code>{s.stop_loss:.6g}</code>\n"
                f"   TP <code>{s.tp1:.6g}</code> / <code>{s.tp2:.6g}</code> / "
                f"<code>{s.tp3:.6g}</code>\n"
                f"   {s.created_at:%d.%m %H:%M} UTC")
        if len(rows) > LIMIT:
            lines.append(f"\n…и ещё {len(rows) - LIMIT}.")
        return "\n".join(lines)

    async def _cmd_report(self) -> str:
        from ..api.forward import forward_vs_backtest
        r = await forward_vs_backtest(min_n=1)
        fw, bt, d = r["forward"], r["backtest"], r["delta"]
        def row(name, key, fmt="+.4f"):
            f = fw.get(key); b = bt.get(key); dd = d.get(key)
            fs = "—" if f is None else format(f, fmt)
            bs = "—" if b is None else format(b, fmt)
            ds = "—" if dd is None else format(dd, fmt)
            return f"{name}: {fs} | {bs} | Δ {ds}"
        return ("📈 <b>Forward vs Backtest</b>\n"
                f"(сделок: {fw['n']} | {bt['n']})\n\n"
                + row("WR%", "winrate", ".2f") + "\n"
                + row("PF", "profit_factor", ".3f") + "\n"
                + row("Exp R", "expectancy_r") + "\n"
                + row("Avg R", "avg_r") + "\n\n"
                f"Вердикт: <b>{r['verdict']}</b>")


telegram_bot = TelegramBot()
