"""Telegram-уведомления: новый сигнал, TP1/TP2, закрытие сделки.

Если TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не заданы — все функции
молча возвращают False, система работает как без Telegram.
Отправка асинхронная, с таймаутом 10с; любая ошибка логируется и
не прерывает сканер/трекер.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import aiohttp

from ..config import get_settings
from ..models import Signal, Direction

if TYPE_CHECKING:  # только для подсказок типов, без циклических импортов
    from ..models import ForwardTestResult

logger = logging.getLogger(__name__)
settings = get_settings()

API = "https://api.telegram.org/bot{token}/{method}"


def telegram_enabled() -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)


async def _send(text: str) -> bool:
    if not telegram_enabled():
        logger.debug("Telegram не настроен — пропуск уведомления")
        return False
    url = API.format(token=settings.telegram_bot_token, method="sendMessage")
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload,
                                    timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.warning("Telegram %s: %s", resp.status, await resp.text())
                    return False
                return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Telegram error: %s", exc)
        return False


# ------------------------------------------------------------------ форматы

def _fmt_new_signal(sig: Signal) -> str:
    emoji = "🟢" if sig.direction == Direction.LONG else "🔴"
    return (
        f"{emoji} <b>НОВЫЙ СИГНАЛ</b>  <code>#{sig.id}</code>\n\n"
        f"Символ: <b>{sig.symbol}</b>\n"
        f"Направление: <b>{sig.direction.value}</b>\n"
        f"Score: <b>{sig.score:.1f}</b>\n"
        f"Grade: <b>{sig.grade}</b>\n"
        f"Confirmations: <b>{sig.confirmations}</b>\n\n"
        f"Вход: <code>{sig.entry:.6g}</code>\n"
        f"SL: <code>{sig.stop_loss:.6g}</code>\n"
        f"TP1: <code>{sig.tp1:.6g}</code>\n"
        f"TP2: <code>{sig.tp2:.6g}</code>\n"
        f"TP3: <code>{sig.tp3:.6g}</code>\n\n"
        f"Время: {sig.created_at:%Y-%m-%d %H:%M} UTC"
    )


def _fmt_tp(sig: Signal, level: int) -> str:
    note = "Сделка переведена в безубыток." if level == 1 else "Частичная фиксация прибыли."
    return (
        f"🟡 <b>TP{level} ДОСТИГНУТ</b>  <code>#{sig.id}</code>\n\n"
        f"{sig.symbol} {sig.direction.value}\n"
        f"{note}"
    )


def _fmt_closed(sig: Signal, res: "ForwardTestResult") -> str:
    emoji = "🟢" if res.final_r > 0 else ("🔴" if res.final_r < 0 else "⚪️")
    hours = res.holding_minutes / 60
    holding = f"{hours:.0f} часов" if hours >= 1 else f"{res.holding_minutes} мин"
    return (
        f"{emoji} <b>СДЕЛКА ЗАКРЫТА</b>  <code>#{sig.id}</code>\n\n"
        f"{sig.symbol} {sig.direction.value}\n"
        f"Результат: <b>{res.result}</b>\n"
        f"Причина: <b>{res.exit_reason}</b>\n"
        f"R: <b>{res.final_r:+.2f}R</b>\n"
        f"Удержание: {holding}\n"
        f"Комиссии: {res.fees_r:.3f}R | Funding: {res.funding_r:+.3f}R"
    )


# ------------------------------------------------------------------ API

async def send_signal_alert(sig: Signal) -> bool:
    return await _send(_fmt_new_signal(sig))


async def send_tp_alert(sig: Signal, level: int) -> bool:
    return await _send(_fmt_tp(sig, level))


async def send_trade_closed_alert(sig: Signal, res: "ForwardTestResult") -> bool:
    return await _send(_fmt_closed(sig, res))


async def send_status_alert(sig: Signal, price: float) -> bool:
    """Совместимость со старым кодом (использовался поллингом mark-price)."""
    status = {"TP1_HIT": "🟡 TP1", "TP2_HIT": "🟡 TP2", "TP3_HIT": "✅ TP3",
              "STOPPED": "🛑 Стоп", "EXPIRED": "⌛️ Истёк"}.get(sig.status.value, sig.status.value)
    return await _send(f"{status} <code>#{sig.id}</code>: <b>{sig.symbol} {sig.direction.value}</b> "
                       f"@ <code>{price:.6g}</code>")


async def send_photo(png: bytes, caption: str = "") -> bool:
    """Отправка PNG через Telegram sendPhoto. no-op без токена."""
    if not telegram_enabled():
        return False
    url = API.format(token=settings.telegram_bot_token, method="sendPhoto")
    form = aiohttp.FormData()
    form.add_field("chat_id", str(settings.telegram_chat_id))
    if caption:
        form.add_field("caption", caption[:1024])
        form.add_field("parse_mode", "HTML")
    form.add_field("photo", png, filename="signal.png", content_type="image/png")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=form,
                                    timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    logger.warning("Telegram sendPhoto %s: %s", resp.status, await resp.text())
                    return False
                return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Telegram sendPhoto error: %s", exc)
        return False
