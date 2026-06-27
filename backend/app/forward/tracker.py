"""Forward-test tracker (инфраструктура наблюдения, торговую логику не меняет).

Закрывает исход каждого опубликованного сигнала ПО ЗАКРЫТЫМ СВЕЧАМ его
таймфрейма той же математикой, что и бэктест-симулятор (portfolio.py):
  • частичные фиксации 50/30/20% на TP1/TP2/TP3 (лимитные, по цене TP);
  • перенос стопа в безубыток после TP1;
  • консервативно: SL приоритетнее TP внутри одного бара;
  • таймаут MAX_HOLD=96 баров → закрытие по close;
  • издержки: taker-комиссия и slippage на рыночных выходах (вход учтён),
    funding каждые 8 часов по историческому ряду.

Результат пишется в forward_test_results; Signal.status обновляется по тем же
событиям (TP1/TP2/TP3/STOPPED/EXPIRED) — поллинг mark-price больше не нужен,
виковые касания не теряются.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from ..backtest.portfolio import CostModel, PARTIALS, MAX_HOLD
from ..binance.client import get_client
from ..config import get_settings
from ..database import SessionLocal
from ..models import Signal, SignalStatus, Direction, ForwardTestResult
from ..notifications.telegram import send_tp_alert, send_trade_closed_alert

logger = logging.getLogger(__name__)
settings = get_settings()

BREAKEVEN_R = 0.05   # |final_r| ниже порога → BREAKEVEN


@dataclass
class Outcome:
    closed: bool
    status: SignalStatus | None = None      # для промежуточных обновлений
    result: str | None = None
    final_r: float = 0.0
    exit_reason: str | None = None
    bars_held: int = 0
    closed_at: datetime | None = None
    tp_hits: dict | None = None
    fees_r: float = 0.0
    funding_r: float = 0.0


def evaluate_outcome(sig: Signal, df: pd.DataFrame,
                     costs: CostModel) -> Outcome:
    """Чистая функция: реплей закрытых свечей df (после created_at) по правилам
    бэктеста. df: open_time/high/low/close, только ЗАКРЫТЫЕ бары."""
    long = sig.direction == Direction.LONG
    entry = sig.entry
    risk = abs(entry - sig.stop_loss)
    if risk <= 0 or df.empty:
        return Outcome(closed=False)

    slip = costs.slip()
    fee = costs.taker_fee
    tps = (sig.tp1, sig.tp2, sig.tp3)
    hits = [False, False, False]
    remaining = 1.0
    realized_r = 0.0
    fees_r = funding_r = 0.0
    cur_sl = sig.stop_loss
    status: SignalStatus | None = None

    # Вход: комиссия + slippage входа в R (как в бэктесте — вход рыночный)
    notional_per_r = entry / risk
    fees_r += fee * notional_per_r            # вход
    realized_r -= fee * notional_per_r
    # slippage входа уже заложен в цену entry в бэктесте; для сигнала вход = entry,
    # поэтому slippage входа учитываем как издержку:
    realized_r -= slip * notional_per_r
    fees_r += slip * notional_per_r

    times = df["open_time"]
    hours = times.dt.hour.values

    def close_part(price: float, part: float, market: bool) -> None:
        nonlocal realized_r, remaining, fees_r
        px = price * (1 - slip) if (long and market) else \
             price * (1 + slip) if (not long and market) else price
        gross_r = ((px - entry) if long else (entry - px)) / risk * part
        f = fee * (px / risk) * part
        realized_r += gross_r - f
        fees_r += f
        remaining -= part

    for j in range(min(len(df), MAX_HOLD)):
        bar = df.iloc[j]
        hi, lo = float(bar["high"]), float(bar["low"])

        # funding каждые 8ч на остаток позиции
        if j > 0 and hours[j] % 8 == 0 and hours[j] != hours[j - 1]:
            rate = costs.funding_at(times.iloc[j])
            pay_r = rate * (float(bar["close"]) / risk) * remaining * (1 if long else -1)
            realized_r -= pay_r
            funding_r += pay_r

        sl_hit = lo <= cur_sl if long else hi >= cur_sl
        if sl_hit:                                   # SL приоритетнее TP
            close_part(cur_sl, remaining, market=True)
            return Outcome(True, None,
                           _classify(realized_r),
                           round(realized_r, 4), "SL", j + 1,
                           times.iloc[j].to_pydatetime().replace(tzinfo=None),
                           {"tp1": hits[0], "tp2": hits[1], "tp3": hits[2]},
                           round(fees_r, 4), round(funding_r, 4))

        for k, tp in enumerate(tps):
            if hits[k]:
                continue
            if (hi >= tp) if long else (lo <= tp):
                close_part(tp, min(PARTIALS[k], remaining), market=False)
                hits[k] = True
                status = (SignalStatus.TP1, SignalStatus.TP2, SignalStatus.TP3)[k]
                if k == 0:
                    cur_sl = entry                   # безубыток
        if hits[2]:
            return Outcome(True, None, _classify(realized_r),
                           round(realized_r, 4), "TP_CASCADE", j + 1,
                           times.iloc[j].to_pydatetime().replace(tzinfo=None),
                           {"tp1": True, "tp2": True, "tp3": True},
                           round(fees_r, 4), round(funding_r, 4))

    if len(df) >= MAX_HOLD:                          # таймаут
        last = df.iloc[MAX_HOLD - 1]
        close_part(float(last["close"]), remaining, market=True)
        return Outcome(True, None, _classify(realized_r),
                       round(realized_r, 4), "TIMEOUT", MAX_HOLD,
                       times.iloc[MAX_HOLD - 1].to_pydatetime().replace(tzinfo=None),
                       {"tp1": hits[0], "tp2": hits[1], "tp3": hits[2]},
                       round(fees_r, 4), round(funding_r, 4))

    return Outcome(False, status=status)             # позиция ещё открыта


def _classify(r: float) -> str:
    if r > BREAKEVEN_R:
        return "WIN"
    if r < -BREAKEVEN_R:
        return "LOSS"
    return "BREAKEVEN"


_STATUS_RANK = {SignalStatus.ACTIVE: 0, SignalStatus.TP1: 1,
                SignalStatus.TP2: 2, SignalStatus.TP3: 3}


async def track_signals() -> None:
    """Цикл наблюдения: для каждого незавершённого сигнала — реплей закрытых
    свечей с момента создания; запись результата при закрытии."""
    client = get_client()
    async with SessionLocal() as db:
        from sqlalchemy import select
        stmt = select(Signal).where(Signal.status.in_(
            [SignalStatus.ACTIVE, SignalStatus.TP1, SignalStatus.TP2]))
        signals = (await db.execute(stmt)).scalars().all()
        if not signals:
            return

        funding_cache: dict[str, pd.Series] = {}
        for sig in signals:
            try:
                start = sig.created_at.replace(tzinfo=timezone.utc)
                df = await client.klines_history(sig.symbol, sig.timeframe, start)
                if df.empty:
                    continue
                # только бары, ОТКРЫТЫЕ после создания сигнала и уже закрытые
                now = datetime.now(timezone.utc)
                df = df[(df["open_time"] > start) & (df["close_time"] <= now)] \
                    .reset_index(drop=True)
                if df.empty:
                    continue

                if sig.symbol not in funding_cache:
                    f = await client.funding_rate_history(sig.symbol, start)
                    funding_cache[sig.symbol] = (
                        f.set_index("fundingTime")["fundingRate"]
                        if not f.empty else None)
                costs = CostModel(funding_series=funding_cache[sig.symbol])

                out = evaluate_outcome(sig, df, costs)

                if not out.closed:
                    if out.status and _STATUS_RANK.get(out.status, 0) > _STATUS_RANK.get(sig.status, 0):
                        sig.status = out.status
                        await db.commit()
                        level = {SignalStatus.TP1: 1, SignalStatus.TP2: 2}.get(out.status, 1)
                        await send_tp_alert(sig, level)
                    continue

                # финальный статус
                final_status = (SignalStatus.STOPPED if out.exit_reason == "SL"
                                else SignalStatus.TP3 if out.exit_reason == "TP_CASCADE"
                                else SignalStatus.EXPIRED)
                # сохранить максимум достигнутого TP в статусе при SL после TP1/TP2
                if out.exit_reason == "SL" and out.tp_hits and out.tp_hits.get("tp2"):
                    final_status = SignalStatus.TP2
                elif out.exit_reason == "SL" and out.tp_hits and out.tp_hits.get("tp1"):
                    final_status = SignalStatus.TP1

                sig.status = final_status
                # ДИАГНОСТИКА: Entry Quality из сохранённых метрик сигнала (не влияет на R)
                try:
                    from ..analysis.entry_quality import compute_entry_quality
                    eq = compute_entry_quality(sig.entry_timing or {},
                                               sig.module_scores or {}).as_dict()
                except Exception:  # noqa: BLE001
                    eq = {}
                from sqlalchemy import select

                existing = (
                    await db.execute(
                        select(ForwardTestResult).where(
                            ForwardTestResult.signal_id == sig.id
                        )
                     )
                ).scalar_one_or_none()

                if existing:
                    sig.status = final_status
                    await db.commit()
                    continue
                res = ForwardTestResult(
                    signal_id=sig.id, symbol=sig.symbol, direction=sig.direction,
                    grade=sig.grade, score=sig.score,
                    entry=sig.entry, stop_loss=sig.stop_loss,
                    tp1=sig.tp1, tp2=sig.tp2, tp3=sig.tp3,
                    opened_at=sig.created_at, closed_at=out.closed_at,
                    holding_minutes=int((out.closed_at - sig.created_at).total_seconds() // 60),
                    bars_held=out.bars_held,
                    result=out.result, final_r=out.final_r,
                    exit_reason=out.exit_reason, tp_hits=out.tp_hits or {},
                    fees_r=out.fees_r, funding_r=out.funding_r,
                    entry_timing=sig.entry_timing or {},
                    entry_quality=eq,
                    active_modules=list((sig.reasons or {}).keys()),
                    filter_diagnostics=sig.filter_diagnostics or {},
                )
                db.add(res)
                await db.commit()
                await send_trade_closed_alert(sig, res)
                # Пост-трейд график (библиотека кейсов) — только визуализация
                try:
                    from ..notifications.chart import render_posttrade_chart
                    from ..notifications.telegram import send_photo
                    png = render_posttrade_chart(res, sig, df)
                    await send_photo(png, caption=f"<code>#{sig.id}</code> <b>{sig.symbol} {sig.direction.value}</b> · "
                                                  f"{out.result} {out.final_r:+.2f}R · {out.exit_reason}")
                except Exception as exc:  # noqa: BLE001 — график не влияет на учёт
                    logger.warning("Пост-трейд график %s #%d: %s", sig.symbol, sig.id, exc)
                logger.info("Forward-результат %s #%d: %s %.3fR (%s)",
                            sig.symbol, sig.id, out.result, out.final_r, out.exit_reason)
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                logger.warning("Трекинг сигнала #%d: %s", sig.id, exc)
