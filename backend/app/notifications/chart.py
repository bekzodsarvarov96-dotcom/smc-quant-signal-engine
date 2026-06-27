"""Визуализация сигнала в PNG (ТОЛЬКО отрисовка, торговую логику не трогает).

Источник данных: свежие klines (запрашиваются при рендере) + поля уже
сохранённого Signal (entry/SL/TP/score/grade/reasons/...). Структурные зоны
(FVG, OB) рисуются из числовых координат, которые модули Сами положили в
текст reasons при анализе — никакого пересчёта стратегии. Структурные
события (BOS/CHOCH/MSS/SWEEP/EQH/EQL) отмечаются, только если соответствующий
модуль присутствует в reasons (т.е. реально участвовал в сигнале).
"""
from __future__ import annotations

import io
import re
from datetime import timezone

import matplotlib
matplotlib.use("Agg")                       # без дисплея, серверный рендер
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd

CANDLES = 180                                # окно отрисовки
FIG_W, FIG_H, DPI = 16, 9, 100              # 1600x900

# тёмная палитра
BG = "#0e1117"; GRID = "#222838"; FG = "#d6deeb"
UP = "#26a69a"; DOWN = "#ef5350"
C_ENTRY = "#00e676"; C_SL = "#ff1744"
C_TP = ["#42a5f5", "#5c6bc0", "#7e57c2"]
C_EMA50 = "#ffb74d"; C_EMA200 = "#ff7043"; C_VWAP = "#26c6da"
C_FVG = "#ffd54f"; C_OB = "#ab47bc"; C_SWEEP = "#ec407a"; C_LIQ = "#90a4ae"


def _parse_zone(text: str) -> tuple[float, float] | None:
    """Достаёт [low–high] из текста reasons FVG/OB (числа с разными тире)."""
    m = re.search(r"\[([\d.]+)\s*[–\-—]\s*([\d.]+)\]", text)
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    return (min(a, b), max(a, b))


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def _vwap(df: pd.DataFrame) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return (tp * df["volume"]).cumsum() / df["volume"].cumsum().replace(0, np.nan)


def render_signal_chart(sig, df: pd.DataFrame,
                        context: dict | None = None) -> bytes:
    """sig — ORM Signal; df — свечи (open_time/open/high/low/close/volume);
    context — опц. {trend, htf_trend, oi, funding, volume} для MARKET CONTEXT.
    Возвращает PNG-байты."""
    df = df.tail(CANDLES).reset_index(drop=True).copy()
    n = len(df)
    x = np.arange(n)
    direction = sig.direction.value if hasattr(sig.direction, "value") else str(sig.direction)
    is_long = direction == "LONG"
    reasons = sig.reasons or {}

    fig = plt.figure(figsize=(FIG_W, FIG_H), dpi=DPI, facecolor=BG)
    # сетка:主 цена слева-широко, объём снизу, правая панель причин
    gs = fig.add_gridspec(5, 5, hspace=0.05, wspace=0.04)
    ax = fig.add_subplot(gs[0:4, 0:4], facecolor=BG)       # цена
    axv = fig.add_subplot(gs[4, 0:4], facecolor=BG, sharex=ax)  # объём
    axp = fig.add_subplot(gs[:, 4], facecolor=BG)          # правая панель
    axp.axis("off")

    # ---------- свечи ----------
    for i in range(n):
        o, h, l, c = df.loc[i, ["open", "high", "low", "close"]]
        col = UP if c >= o else DOWN
        ax.plot([i, i], [l, h], color=col, linewidth=0.8, zorder=2)
        ax.add_patch(Rectangle((i - 0.3, min(o, c)), 0.6, abs(c - o) or h*1e-9,
                               facecolor=col, edgecolor=col, zorder=3))

    # ---------- индикаторы ----------
    if n >= 50:
        ax.plot(x, _ema(df["close"], 50), color=C_EMA50, lw=1.2, label="EMA50", zorder=4)
    if n >= 200:
        ax.plot(x, _ema(df["close"], 200), color=C_EMA200, lw=1.2, label="EMA200", zorder=4)
    ax.plot(x, _vwap(df), color=C_VWAP, lw=1.1, ls="--", label="VWAP", zorder=4)

    # ---------- уровни сделки ----------
    levels = [(sig.entry, C_ENTRY, "ENTRY"), (sig.stop_loss, C_SL, "SL"),
              (sig.tp1, C_TP[0], "TP1"), (sig.tp2, C_TP[1], "TP2"),
              (sig.tp3, C_TP[2], "TP3")]
    for price, col, lab in levels:
        ax.axhline(price, color=col, lw=1.4, ls="-", alpha=0.9, zorder=5)
        ax.text(n - 8, price, f"{lab} {price:.6g}", color=col, fontsize=9,
                va="center", ha="left", fontweight="bold",
                bbox=dict(fc=BG, ec=col, lw=0.8, pad=1.2))

    # ---------- FVG / OB зоны из reasons ----------
    if "fvg" in reasons:
        for t in reasons["fvg"]:
            z = _parse_zone(t)
            if z:
                ax.add_patch(Rectangle((n*0.55, z[0]), n*0.45, z[1]-z[0],
                             facecolor=C_FVG, alpha=0.16, edgecolor=C_FVG, lw=1, zorder=1))
                ax.text(n*0.56, z[1], "FVG", color=C_FVG, fontsize=9, fontweight="bold", va="bottom")
    if "order_block" in reasons:
        for t in reasons["order_block"]:
            z = _parse_zone(t)
            if z:
                ax.add_patch(Rectangle((n*0.5, z[0]), n*0.5, z[1]-z[0],
                             facecolor=C_OB, alpha=0.14, edgecolor=C_OB, lw=1, ls="--", zorder=1))
                ax.text(n*0.51, z[0], "OB", color=C_OB, fontsize=9, fontweight="bold", va="top")

    # ---------- структурные события (если модуль участвовал) ----------
    highs, lows, closes = df["high"].values, df["low"].values, df["close"].values
    def mark(label, color, at_low: bool):
        """Стрелка+подпись у недавнего экстремума."""
        idx = n - 1 - int(np.argmax(closes[::-1] != closes[-1])) if n > 5 else n - 1
        idx = max(n - 12, min(idx, n - 2))
        y = lows[idx] if at_low else highs[idx]
        off = (highs.max() - lows.min()) * 0.03
        ax.annotate(label, xy=(idx, y), xytext=(idx, y - off*4 if at_low else y + off*4),
                    color=color, fontsize=10, fontweight="bold", ha="center",
                    arrowprops=dict(arrowstyle="->", color=color, lw=1.6), zorder=6)
    if "bos" in reasons: mark("BOS", "#00bcd4", not is_long)
    if "choch" in reasons: mark("CHOCH", "#ffa726", is_long)
    if "mss" in reasons: mark("MSS", "#66bb6a", not is_long)

    # SWEEP: точная свеча из setup_signature.sweep, если есть
    import json

    # Безопасно читаем setup_signature
    signature = sig.setup_signature

    if isinstance(signature, str):
        try:
            signature = json.loads(signature)
        except Exception:
            signature = {}

    if not isinstance(signature, dict):
        signature = {}
    sweep_price = signature.get("sweep")
    if "liquidity_sweep" in reasons and sweep_price:
        # ближайший по экстремуму бар
        arr = lows if is_long else highs
        j = int(np.argmin(np.abs(arr - sweep_price)))
        ax.scatter([j], [arr[j]], marker="v" if not is_long else "^",
                   s=140, color=C_SWEEP, zorder=7, edgecolor="white", lw=0.6)
        ax.text(j, arr[j], " SWEEP", color=C_SWEEP, fontsize=9, fontweight="bold",
                va="top" if is_long else "bottom")

    # EQH/EQL: уровень равных хаёв/лоёв
    if "equal_highs_lows" in reasons:
        lab = "EQH" if not is_long else "EQL"
        lvl = highs[-20:].max() if not is_long else lows[-20:].min()
        ax.axhline(lvl, color=C_LIQ, lw=1.0, ls=":", alpha=0.8, zorder=4)
        ax.text(2, lvl, lab, color=C_LIQ, fontsize=9, fontweight="bold", va="bottom")

    # ---------- объём ----------
    vc = [UP if df.loc[i,"close"]>=df.loc[i,"open"] else DOWN for i in range(n)]
    axv.bar(x, df["volume"], color=vc, width=0.7, alpha=0.7)
    axv.set_ylabel("Vol", color=FG, fontsize=8)

    # ---------- косметика осей ----------
    for a in (ax, axv):
        a.set_xlim(-1, n + 6)
        a.grid(True, color=GRID, lw=0.5, alpha=0.5)
        a.tick_params(colors=FG, labelsize=8)
        for s in a.spines.values(): s.set_color(GRID)
    ax.legend(loc="upper center", ncol=3, fontsize=8, facecolor=BG,
              edgecolor=GRID, labelcolor=FG)
    plt.setp(ax.get_xticklabels(), visible=False)

    # ---------- инфо-панель (верх-лево) ----------
    prob = int(round((sig.probability or 0) * 100))
    info = (f"{sig.symbol}   #{sig.id}\n\n{direction}\n\nScore: {sig.score:.1f}\n"
            f"Grade: {sig.grade}\n\nConfirmations: {sig.confirmations}\n"
            f"Probability: {prob}%")
    ax.text(0.012, 0.985, info, transform=ax.transAxes, fontsize=10.5,
            color=FG, va="top", ha="left", fontfamily="monospace", fontweight="bold",
            bbox=dict(fc="#161b26", ec=C_ENTRY if is_long else C_SL, lw=1.5, pad=6),
            zorder=10)

    # ---------- панель причин (справа) ----------
    PRETTY = {"mtf_trend":"HTF Trend","market_structure":"Market Structure","bos":"BOS",
              "choch":"CHOCH","mss":"MSS","liquidity_sweep":"Liquidity Sweep",
              "equal_highs_lows":"Equal H/L","order_block":"Order Block","fvg":"FVG",
              "fibonacci":"Fibonacci","volume_profile":"Volume Profile","volume":"Volume Spike",
              "cvd":"CVD","vwap":"VWAP","open_interest":"Open Interest","oi_delta":"OI Delta",
              "funding_rate":"Funding","liquidation_clusters":"Liquidations",
              "rsi_divergence":"RSI Divergence","ema_trend":"EMA Trend"}
    lines = ["SIGNAL ANALYSIS", ""]
    for name in reasons:
        lines.append(f"\u2713 {PRETTY.get(name, name)}")
    axp.text(0.0, 0.99, "\n".join(lines), transform=axp.transAxes, fontsize=11,
             color="#9ccc65", va="top", ha="left", fontfamily="monospace", fontweight="bold")

    # MARKET CONTEXT (если передан)
    if context:
        def b(v, pos="Bullish", neg="Bearish"):
            return pos if v in (True,"up","rising","positive","high") else neg if v is not None else "—"
        ctx_lines = ["", "", "MARKET CONTEXT", "",
                     f"Trend:   {b(context.get('trend'))}",
                     f"HTF:     {b(context.get('htf_trend'))}",
                     f"OI:      {b(context.get('oi'),'Rising','Falling')}",
                     f"Funding: {b(context.get('funding'),'Positive','Negative')}",
                     f"Volume:  {b(context.get('volume'),'High','Normal')}"]
        axp.text(0.0, 0.40, "\n".join(ctx_lines), transform=axp.transAxes, fontsize=10,
                 color="#80cbc4", va="top", ha="left", fontfamily="monospace")

    # ---------- ENTRY QUALITY (диагностика) ----------
    eq_energy = getattr(sig, "energy_score", None)
    eq_verdict = getattr(sig, "entry_verdict", "") or ""
    if eq_verdict:
        vcolor = {"EARLY ENTRY": "#00e676", "GOOD ENTRY": "#9ccc65",
                  "LATE ENTRY": "#ffa726", "VERY LATE ENTRY": "#ff5252"}.get(eq_verdict, FG)
        eq_lines = ["", "", "ENTRY QUALITY", "",
                    f"Energy Score:   {eq_energy:.0f}",
                    f"Pressure Score: {getattr(sig,'pressure_score',0):.0f}",
                    f"Late Risk:      {getattr(sig,'late_risk',0):.0f}", "",
                    f"Verdict:", f"{eq_verdict}"]
        axp.text(0.0, 0.14, "\n".join(eq_lines), transform=axp.transAxes, fontsize=10,
                 color=vcolor, va="top", ha="left", fontfamily="monospace", fontweight="bold")

    # ---------- нижняя панель: Entry/SL/TP/RR ----------
    risk = abs(sig.entry - sig.stop_loss)
    rr = abs(sig.tp1 - sig.entry) / risk if risk > 0 else 0
    bottom = (f"Entry {sig.entry:.6g}   SL {sig.stop_loss:.6g}   "
              f"TP1 {sig.tp1:.6g}   TP2 {sig.tp2:.6g}   TP3 {sig.tp3:.6g}   "
              f"RR(TP1) 1:{rr:.2f}")
    fig.text(0.5, 0.012, bottom, color=FG, fontsize=11, ha="center",
             fontfamily="monospace", fontweight="bold",
             bbox=dict(fc="#161b26", ec=GRID, lw=1, pad=4))

    fig.suptitle(f"#{sig.id}  {sig.symbol} {direction} — SMC Signal", color=FG,
                 fontsize=14, fontweight="bold", y=0.965)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG, dpi=DPI)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def render_posttrade_chart(res, sig, df: pd.DataFrame) -> bytes:
    """Пост-трейд график (библиотека кейсов): Entry/SL/TP + реальное движение
    после входа + блок RESULT + пост-трейд аналитика Entry Quality.
    res — ForwardTestResult; sig — Signal; df — свечи, охватывающие сделку."""
    df = df.tail(CANDLES).reset_index(drop=True).copy()
    n = len(df)
    x = np.arange(n)
    direction = sig.direction.value if hasattr(sig.direction, "value") else str(sig.direction)
    is_long = direction == "LONG"

    fig = plt.figure(figsize=(FIG_W, FIG_H), dpi=DPI, facecolor=BG)
    gs = fig.add_gridspec(5, 5, hspace=0.05, wspace=0.04)
    ax = fig.add_subplot(gs[0:4, 0:4], facecolor=BG)
    axv = fig.add_subplot(gs[4, 0:4], facecolor=BG, sharex=ax)
    axp = fig.add_subplot(gs[:, 4], facecolor=BG)
    axp.axis("off")

    for i in range(n):
        o, h, l, c = df.loc[i, ["open", "high", "low", "close"]]
        col = UP if c >= o else DOWN
        ax.plot([i, i], [l, h], color=col, linewidth=0.8, zorder=2)
        ax.add_patch(Rectangle((i - 0.3, min(o, c)), 0.6, abs(c - o) or h*1e-9,
                               facecolor=col, edgecolor=col, zorder=3))

    # маркер входа: ищем бар у времени open
    try:
        opened = pd.Timestamp(res.opened_at, tz="UTC") if res.opened_at.tzinfo is None else res.opened_at
        entry_idx = int((df["open_time"] <= opened).sum()) - 1
        entry_idx = max(0, min(entry_idx, n - 1))
    except Exception:  # noqa: BLE001
        entry_idx = n // 3
    ax.axvline(entry_idx, color="#ffffff", lw=1.0, ls=":", alpha=0.5, zorder=4)
    ax.scatter([entry_idx], [sig.entry], marker="o", s=120, color="#ffffff",
               edgecolor="#000000", lw=0.8, zorder=8)
    ax.text(entry_idx, sig.entry, " ENTRY", color="#ffffff", fontsize=9,
            fontweight="bold", va="bottom")

    for price, col, lab in [(sig.entry, C_ENTRY, "ENTRY"), (sig.stop_loss, C_SL, "SL"),
                            (sig.tp1, C_TP[0], "TP1"), (sig.tp2, C_TP[1], "TP2"),
                            (sig.tp3, C_TP[2], "TP3")]:
        ax.axhline(price, color=col, lw=1.3, alpha=0.85, zorder=5)
        ax.text(n - 8, price, f"{lab} {price:.6g}", color=col, fontsize=9,
                va="center", fontweight="bold", bbox=dict(fc=BG, ec=col, lw=0.8, pad=1.2))

    vc = [UP if df.loc[i,"close"]>=df.loc[i,"open"] else DOWN for i in range(n)]
    axv.bar(x, df["volume"], color=vc, width=0.7, alpha=0.7)
    axv.set_ylabel("Vol", color=FG, fontsize=8)
    for a in (ax, axv):
        a.set_xlim(-1, n + 6); a.grid(True, color=GRID, lw=0.5, alpha=0.5)
        a.tick_params(colors=FG, labelsize=8)
        for s in a.spines.values(): s.set_color(GRID)
    plt.setp(ax.get_xticklabels(), visible=False)

    # ---------- блок RESULT (крупно) ----------
    rcol = {"WIN": "#00e676", "LOSS": "#ff5252", "BREAKEVEN": "#90a4ae"}.get(res.result, FG)
    axp.text(0.5, 0.96, "RESULT", transform=axp.transAxes, fontsize=13, color=FG,
             ha="center", va="top", fontfamily="monospace", fontweight="bold")
    axp.text(0.5, 0.88, res.result, transform=axp.transAxes, fontsize=22, color=rcol,
             ha="center", va="top", fontweight="bold")
    hours = (res.holding_minutes or 0) / 60
    info = [f"Final R:    {res.final_r:+.2f}R",
            f"Holding:    {hours:.0f}h" if hours >= 1 else f"Holding:    {res.holding_minutes}m",
            f"Exit:       {res.exit_reason}"]
    axp.text(0.0, 0.72, "\n".join(info), transform=axp.transAxes, fontsize=12,
             color=FG, va="top", ha="left", fontfamily="monospace", fontweight="bold")

    # ---------- пост-трейд Entry Quality ----------
    eq = res.entry_quality or {}
    verdict = eq.get("entry_verdict", "") or getattr(sig, "entry_verdict", "") or "—"
    vcolor = {"EARLY ENTRY": "#00e676", "GOOD ENTRY": "#9ccc65",
              "LATE ENTRY": "#ffa726", "VERY LATE ENTRY": "#ff5252"}.get(verdict, FG)
    eq_lines = ["", "ENTRY QUALITY", "",
                f"Verdict: ", f"{verdict}", "",
                f"Energy:   {eq.get('energy_score', getattr(sig,'energy_score',0)):.0f}",
                f"Pressure: {eq.get('pressure_score', getattr(sig,'pressure_score',0)):.0f}",
                f"Late Risk:{eq.get('late_risk', getattr(sig,'late_risk',0)):.0f}"]
    axp.text(0.0, 0.34, "\n".join(eq_lines), transform=axp.transAxes, fontsize=11,
             color=vcolor, va="top", ha="left", fontfamily="monospace", fontweight="bold")

    fig.suptitle(f"#{sig.id}  {sig.symbol} {direction} — {res.result} ({res.final_r:+.2f}R)",
                 color=rcol, fontsize=14, fontweight="bold", y=0.965)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG, dpi=DPI)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
