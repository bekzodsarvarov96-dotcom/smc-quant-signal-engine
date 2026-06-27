"""Аудит активаций модулей (read-only): почему cvd / open_interest / oi_delta /
funding_rate / fibonacci не участвуют в сделках.

Ничего в стратегии не меняет. Прогоняет 24 мес данных, собирает:
  • активации каждого модуля (score>0);
  • воронки условий для целевых модулей (на каком шаге отбраковка);
  • распределения ключевых величин → проекции активаций при новых порогах;
  • влияние на кандидатов: семейства flow/sentiment и отказ по min_families.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.statistical_truth import make_synthetic                  # noqa: E402
from app.backtest.precompute import WINDOW, WARMUP, _resample_htf     # noqa: E402
from app.analysis.base import MarketContext, atr, rsi, get_swing_points  # noqa: E402
from app.analysis.registry import ALL_MODULES                         # noqa: E402
from app.analysis.market_structure import detect_structure            # noqa: E402
from app.engine.families import family_votes, FAMILY_MAP              # noqa: E402

MONTHS = 24
TARGETS = ("cvd", "open_interest", "oi_delta", "funding_rate", "fibonacci")
FIB_LEVELS = (0.5, 0.618, 0.705, 0.786)


def audit(symbol: str = "BTCUSDT", seed: int = 100) -> dict:
    df = make_synthetic(symbol, MONTHS, seed=seed)
    n = len(df)

    activations = Counter()            # module -> n активных баров
    bars_total = 0

    fib = Counter()                    # воронка fibonacci
    fib_min_dist: list[float] = []     # мин. расстояние до уровня в ATR (при тренде+порядке)

    cvd_c = Counter()
    cvd_strengths: list[float] = []    # |cvd_strength| на каждом баре
    cvd_signed: list[tuple[float, float]] = []  # (strength, price_chg)
    cvd_div_gap: list[float] = []      # дефицит CVD на экстремуме в долях vol_norm

    fam_counters = Counter()           # подтверждения семейств у кандидатов
    cand_total = 0
    cand_conf2_no_sent_flow = 0        # кандидаты с 2 семействами, где flow и sentiment молчат
    cand_conf2 = 0

    weights = {m.name: m.weight for m in ALL_MODULES}
    t0 = time.time()

    for i in range(WARMUP, n - 1):
        lo = max(0, i + 1 - WINDOW)
        window = df.iloc[lo: i + 1].reset_index(drop=True)
        htf = _resample_htf(window, "1h")
        ctx = MarketContext(symbol=symbol, timeframe="1h", df=window, htf_df=htf)

        results = [m.analyze(ctx) for m in ALL_MODULES]
        bars_total += 1
        for r in results:
            if r.score > 0:
                activations[r.name] += 1

        # ---------- воронка FIBONACCI ----------
        swings = get_swing_points(ctx)
        st = detect_structure(ctx)
        if not swings["highs"] or not swings["lows"]:
            fib["no_swings"] += 1
        elif st["trend"] == "range":
            fib["trend_range"] += 1
        else:
            hi_idx, hi = swings["highs"][-1]
            lo_idx, lo_p = swings["lows"][-1]
            order_ok = (st["trend"] == "up" and lo_idx < hi_idx) or \
                       (st["trend"] == "down" and hi_idx < lo_idx)
            if hi == lo_p:
                fib["degenerate"] += 1
            elif not order_ok:
                fib["wrong_swing_order"] += 1
            else:
                price = window["close"].iloc[-1]
                a = atr(window).iloc[-1]
                if st["trend"] == "up":
                    targets = [hi - (hi - lo_p) * l for l in FIB_LEVELS]
                else:
                    targets = [lo_p + (hi - lo_p) * l for l in FIB_LEVELS]
                dist = min(abs(price - t) for t in targets) / max(a, 1e-12)
                fib_min_dist.append(dist)
                if dist <= 0.35:
                    fib["activated"] += 1
                else:
                    fib["tolerance_miss"] += 1

        # ---------- воронка CVD ----------
        w = window
        if "taker_buy_base" not in w.columns or len(w) < 60:
            cvd_c["no_data"] += 1
        else:
            delta = 2 * w["taker_buy_base"] - w["volume"]
            cvd = delta.cumsum()
            look = 20
            vol_norm = w["volume"].iloc[-look:].sum()
            if vol_norm <= 0:
                cvd_c["zero_volume"] += 1
            else:
                price_chg = (w["close"].iloc[-1] - w["close"].iloc[-look]) / w["close"].iloc[-look]
                strength = (cvd.iloc[-1] - cvd.iloc[-look]) / vol_norm
                cvd_strengths.append(abs(strength))
                cvd_signed.append((strength, price_chg))

                win_p = w["close"].iloc[-look:]
                win_c = cvd.iloc[-look:]
                p_hh = win_p.iloc[-1] >= win_p.max() * 0.999
                p_ll = win_p.iloc[-1] <= win_p.min() * 1.001
                if p_hh or p_ll:
                    gap = (win_c.max() - win_c.iloc[-1]) if p_hh else (win_c.iloc[-1] - win_c.min())
                    cvd_div_gap.append(gap / vol_norm)
                    if gap > 0.15 * vol_norm:
                        cvd_c["activated_divergence"] += 1
                        continue
                if abs(price_chg) < 0.002 and abs(strength) > 0.12:
                    cvd_c["activated_absorption"] += 1
                elif abs(strength) > 0.08 and strength * price_chg > 0:
                    cvd_c["activated_flow"] += 1
                elif p_hh or p_ll:
                    cvd_c["extreme_but_gap_small"] += 1
                elif abs(strength) <= 0.08:
                    cvd_c["strength_below_0.08"] += 1
                else:
                    cvd_c["sign_mismatch"] += 1

        # ---------- семейства у кандидатов ----------
        active = [r for r in results if r.score > 0.05]
        if active:
            directional = sum(weights[r.name] * r.bias * r.score for r in active)
            if abs(directional) > 1e-9:
                cand_total += 1
                sign = 1.0 if directional > 0 else -1.0
                votes = family_votes(results, weights, sign)
                conf = [f for f, v in votes.items() if v.confirmed]
                for f in conf:
                    fam_counters[f] += 1
                if len(conf) == 2:
                    cand_conf2 += 1
                    if "flow" not in conf and "sentiment" not in conf:
                        cand_conf2_no_sent_flow += 1

        if i % 4000 == 0:
            print(f"  {i}/{n} ({time.time()-t0:.0f}s)", flush=True)

    # ---------- проекции порогов ----------
    fd = np.array(fib_min_dist) if fib_min_dist else np.array([99.0])
    fib_proj = {f"tol={t}*ATR": int((fd <= t).sum())
                for t in (0.35, 0.5, 0.75, 1.0)}

    cs = np.array(cvd_strengths) if cvd_strengths else np.array([0.0])
    sg = np.array(cvd_signed) if cvd_signed else np.zeros((1, 2))
    flow_proj = {f"strength>{t}": int(((np.abs(sg[:, 0]) > t) & (sg[:, 0] * sg[:, 1] > 0)).sum())
                 for t in (0.08, 0.05, 0.03, 0.02)}
    gaps = np.array(cvd_div_gap) if cvd_div_gap else np.array([0.0])
    div_proj = {f"gap>{t}": int((gaps > t).sum()) for t in (0.15, 0.10, 0.05, 0.03)}

    return {
        "bars_total": bars_total,
        "candidates_total": cand_total,
        "activations": dict(activations),
        "fib_funnel": dict(fib), "fib_dist_pct": {
            "p50": round(float(np.percentile(fd, 50)), 2),
            "p25": round(float(np.percentile(fd, 25)), 2),
            "p10": round(float(np.percentile(fd, 10)), 2)},
        "fib_projection": fib_proj,
        "cvd_funnel": dict(cvd_c),
        "cvd_strength_pct": {
            "p50": round(float(np.percentile(cs, 50)), 4),
            "p90": round(float(np.percentile(cs, 90)), 4),
            "p99": round(float(np.percentile(cs, 99)), 4)},
        "cvd_flow_projection": flow_proj,
        "cvd_div_gap_pct": {
            "p50": round(float(np.percentile(gaps, 50)), 4),
            "p90": round(float(np.percentile(gaps, 90)), 4)},
        "cvd_div_projection": div_proj,
        "family_confirms": dict(fam_counters),
        "cand_conf2": cand_conf2,
        "cand_conf2_no_sent_flow": cand_conf2_no_sent_flow,
    }


if __name__ == "__main__":
    out = audit()
    Path("/home/claude/module_audit_raw.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps(out, indent=2, ensure_ascii=False))
