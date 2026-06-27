"""Attribution Analysis: вклад модулей в результат системы.

Два уровня анализа:

1. ТРЕЙД-УРОВЕНЬ (module_performance) — по закрытым сделкам бэктестов:
   для каждого модуля сравниваются сделки, где он был активен «за» сигнал,
   со сделками без него. contribution_score = разница expectancy
   (актив − неактив): сколько R на сделку модуль добавляет своим присутствием.

2. РЕ-СКОРИНГ ПОДМНОЖЕСТВ (leave-one-out + greedy) — честный пересчёт
   score/семейств кандидатов БЕЗ выбранных модулей по сырым (bias, score)
   из module_raw, затем полная портфельная симуляция с издержками.
   Кандидаты, у которых при удалении модулей переворачивается направление,
   отбрасываются (их entry/SL/TP считались для исходного направления).
   IS = первые 70% истории (на них greedy выбирает подмножество),
   OOS = последние 30% (на них подмножество только проверяется).

Датасет (df + кандидаты) кэшируется в памяти при каждом ручном бэктесте
(POST /api/v1/backtest/run) — см. backtester.py.
"""
from __future__ import annotations

import logging
import math
from dataclasses import replace

import numpy as np
import pandas as pd

from ..analysis.registry import ALL_MODULES
from ..engine.families import FAMILY_MAP, FAMILIES
from ..engine.scoring import SignalCandidate, load_grade_cutoffs
from .portfolio import CostModel, simulate_portfolio, compute_metrics

logger = logging.getLogger(__name__)

WEIGHTS = {m.name: m.weight for m in ALL_MODULES}

# Кэш датасетов для комбинаторного анализа: {(symbol, timeframe): dict}
DATASET_CACHE: dict[tuple[str, str], dict] = {}
DATASET_CACHE_MAX = 3

ACTIVE_THR = 0.1          # |вклад| выше — модуль считается активным в сделке
MIN_TRADES_BUCKET = 10    # минимум сделок для статистики по модулю


def cache_dataset(symbol: str, timeframe: str, df: pd.DataFrame,
                  candidates: list[SignalCandidate],
                  funding: pd.Series | None) -> None:
    key = (symbol.upper(), timeframe)
    DATASET_CACHE[key] = {"df": df, "candidates": candidates, "funding": funding}
    while len(DATASET_CACHE) > DATASET_CACHE_MAX:
        DATASET_CACHE.pop(next(iter(DATASET_CACHE)))
    logger.info("Attribution dataset cached: %s %s (%d кандидатов)",
                symbol, timeframe, len(candidates))


# ------------------------------------------------------------------ уровень 1

def _pf(rs: np.ndarray) -> float:
    gw = rs[rs > 0].sum()
    gl = abs(rs[rs <= 0].sum())
    if gl == 0:
        return 999.0 if gw > 0 else 0.0
    return round(float(gw / gl), 3)


def module_performance(trades: list[dict]) -> list[dict]:
    """trades — список dict-сделок (из BacktestRun.trades) с полем modules."""
    out = []
    all_rs = np.array([t["pnl_r"] for t in trades]) if trades else np.array([])
    for m in ALL_MODULES:
        name = m.name
        act, inact = [], []
        for t in trades:
            contrib = (t.get("modules") or {}).get(name, 0.0)
            (act if contrib > ACTIVE_THR else inact).append(t["pnl_r"])
        a = np.array(act)
        i = np.array(inact)
        if len(a) == 0:
            out.append({"module": name, "family": FAMILY_MAP.get(name),
                        "total_signals": 0, "winrate": None, "average_r": None,
                        "expectancy": None, "profit_factor": None,
                        "contribution_score": None,
                        "note": "ни одной сделки с активным модулем"})
            continue
        exp_a = float(a.mean())
        exp_i = float(i.mean()) if len(i) >= MIN_TRADES_BUCKET else \
            (float(all_rs.mean()) if len(all_rs) else 0.0)
        out.append({
            "module": name,
            "family": FAMILY_MAP.get(name),
            "total_signals": int(len(a)),
            "winrate": round(float((a > 0).mean() * 100), 2),
            "average_r": round(exp_a, 4),
            "expectancy": round(exp_a, 4),
            "profit_factor": _pf(a),
            "contribution_score": round(exp_a - exp_i, 4),
            "sample_inactive": int(len(i)),
        })
    out.sort(key=lambda x: (x["contribution_score"] is None,
                            -(x["contribution_score"] or 0)))
    return out


# ------------------------------------------------------------------ уровень 2

def rescore(cand: SignalCandidate, disabled: set[str],
            cutoffs: dict[str, float]) -> SignalCandidate | None:
    """Пересчёт score/направления/семейств кандидата без модулей disabled.

    Математика идентична engine.scoring.evaluate. Возвращает None, если
    после удаления модулей направление перевернулось или активных не осталось.
    """
    raw = {n: (v[0], v[1], (v[2] if len(v) > 2 else 0))
           for n, v in cand.module_raw.items() if n not in disabled}
    if not raw:
        return None
    subset_total_w = sum(w for n, w in WEIGHTS.items() if n not in disabled)

    active = {n: (b, s) for n, (b, s, _) in raw.items() if s > 0.05}
    if not active:
        return None
    act_mag = sum(WEIGHTS[n] * s for n, (b, s) in active.items())
    directional = sum(WEIGHTS[n] * b * s for n, (b, s) in active.items())
    if act_mag <= 0:
        return None

    new_dir = "LONG" if directional > 0 else "SHORT"
    if new_dir != cand.direction:
        return None                      # уровни риска считались для другой стороны

    sign = 1.0 if directional > 0 else -1.0
    alignment = directional / act_mag
    participation = sum(WEIGHTS[n] for n in active) / subset_total_w
    score = abs(alignment) * math.sqrt(min(participation / 0.45, 1.0)) * 100.0

    # Семейства на подмножестве (no_data-модули вне знаменателя — как в families.py)
    n_conf, n_conflict = 0, 0
    fam_scores: dict[str, float] = {}
    for fam in FAMILIES:
        members = [(n, raw[n]) for n in raw
                   if FAMILY_MAP.get(n) == fam and not raw[n][2]]
        if not members:
            fam_scores[fam] = 0.0
            continue
        wsum = sum(WEIGHTS[n] for n, _ in members)
        f = sum(WEIGHTS[n] * b * s for n, (b, s, _) in members) / wsum
        fam_scores[fam] = round(f, 3)
        has_active = any(b * sign > 0.3 and s > 0.3 for _, (b, s, _) in members)
        if f * sign > 0.15 and has_active:
            n_conf += 1
        elif f * sign < -0.15:
            n_conflict += 1

    score *= max(0.0, 1.0 - 0.15 * n_conflict)
    score = round(min(100.0, score), 1)

    from ..engine.scoring import grade_of
    return replace(cand, score=score, confirmations=n_conf,
                   families=fam_scores,
                   grade=grade_of(score, n_conf, True, cutoffs))


def subset_metrics(df: pd.DataFrame, candidates: list[SignalCandidate],
                   disabled: set[str], costs: CostModel,
                   min_score: float, min_fam: int,
                   is_fraction: float = 0.7) -> dict:
    """IS/OOS метрики системы без модулей disabled (полная симуляция)."""
    cutoffs = load_grade_cutoffs()
    rescored = []
    for c in candidates:
        r = rescore(c, disabled, cutoffs) if disabled else c
        if r is not None and r.score >= min_score and r.confirmations >= min_fam:
            rescored.append(r)

    split = int(len(df) * is_fraction)

    def run(lo: int, hi: int) -> dict:
        seg = df.iloc[lo:hi].reset_index(drop=True)
        cs = []
        for c in rescored:
            if lo <= c.bar_index < hi:
                c2 = replace(c, bar_index=c.bar_index - lo)
                cs.append(c2)
        res = simulate_portfolio(seg, cs, costs)
        return res.metrics

    return {"is": run(0, split), "oos": run(split, len(df)),
            "n_candidates": len(rescored)}


def leave_one_out(df: pd.DataFrame, candidates: list[SignalCandidate],
                  costs: CostModel, min_score: float, min_fam: int,
                  base_disabled: set[str] | None = None) -> dict:
    """Δ-метрики OOS при удалении каждого модуля относительно базовой системы."""
    base_disabled = base_disabled or set()
    base = subset_metrics(df, candidates, base_disabled, costs, min_score, min_fam)
    base_oos = base["oos"]

    rows = []
    for m in ALL_MODULES:
        if m.name in base_disabled:
            continue
        sub = subset_metrics(df, candidates, base_disabled | {m.name},
                             costs, min_score, min_fam)
        oos = sub["oos"]
        d_pf = (oos["profit_factor"] - base_oos["profit_factor"]
                if oos["n_trades"] and base_oos["n_trades"] else 0.0)
        d_exp = oos["expectancy_r"] - base_oos["expectancy_r"]
        # Классификация: модуль улучшает PF, если его УДАЛЕНИЕ ухудшает PF
        if oos["n_trades"] < MIN_TRADES_BUCKET or base_oos["n_trades"] < MIN_TRADES_BUCKET:
            verdict = "insufficient_data"
        elif d_pf < -0.05 and d_exp < -0.005:
            verdict = "improves_pf"        # удаление вредит → модуль полезен
        elif d_pf > 0.05 and d_exp > 0.005:
            verdict = "worsens_pf"         # удаление помогает → модуль вреден
        else:
            verdict = "statistically_useless"
        rows.append({
            "module": m.name, "family": FAMILY_MAP.get(m.name),
            "oos_pf_without": oos["profit_factor"],
            "oos_expectancy_without": oos["expectancy_r"],
            "delta_pf_on_removal": round(d_pf, 3),
            "delta_expectancy_on_removal": round(d_exp, 4),
            "oos_trades_without": oos["n_trades"],
            "verdict": verdict,
        })
    rows.sort(key=lambda r: r["delta_pf_on_removal"])
    return {"baseline": base, "modules": rows}


def greedy_backward(df: pd.DataFrame, candidates: list[SignalCandidate],
                    costs: CostModel, min_score: float, min_fam: int,
                    protected: set[str] = frozenset({"mtf_trend"}),
                    max_removals: int = 8) -> dict:
    """Жадное обратное исключение: на каждом шаге убираем модуль, удаление
    которого максимально повышает IS-expectancy; выбор делается ТОЛЬКО на
    In-Sample, итог проверяется на Out-of-Sample. protected не удаляются
    (mtf_trend держит HTF-фильтр)."""
    disabled: set[str] = set()
    history = []
    cur = subset_metrics(df, candidates, disabled, costs, min_score, min_fam)
    cur_is_exp = cur["is"]["expectancy_r"]

    for step in range(max_removals):
        best_name, best_exp, best_metrics = None, cur_is_exp, None
        for m in ALL_MODULES:
            if m.name in disabled or m.name in protected:
                continue
            trial = subset_metrics(df, candidates, disabled | {m.name},
                                   costs, min_score, min_fam)
            if trial["is"]["n_trades"] < MIN_TRADES_BUCKET:
                continue
            if trial["is"]["expectancy_r"] > best_exp + 1e-4:
                best_name, best_exp, best_metrics = m.name, trial["is"]["expectancy_r"], trial
        if best_name is None:
            break
        disabled.add(best_name)
        cur, cur_is_exp = best_metrics, best_exp
        history.append({"step": step + 1, "removed": best_name,
                        "is_expectancy": round(best_exp, 4),
                        "is_pf": cur["is"]["profit_factor"]})
        logger.info("Greedy шаг %d: удалён %s → IS exp=%.4f", step + 1, best_name, best_exp)

    out = {
        "disabled_modules": sorted(disabled),
        "enabled_modules": [m.name for m in ALL_MODULES if m.name not in disabled],
        "history": history,
        "final_is": cur["is"],
        "final_oos": cur["oos"],      # единственная честная оценка результата
        "recommendation_env": "DISABLED_MODULES=" + ",".join(sorted(disabled)),
    }
    warnings = []
    if cur["oos"]["n_trades"] < 30:
        warnings.append(
            f"OOS-сделок всего {cur['oos']['n_trades']} (<30): результат greedy "
            "статистически НЕ значим — высокий риск подгонки под шум. "
            "Не применяйте рекомендацию без проверки на большем периоде/символах.")
    if cur["is"]["n_trades"] < 30:
        warnings.append(f"IS-сделок {cur['is']['n_trades']} (<30): выбор подмножества ненадёжен.")
    if len(history) >= 3 and cur["oos"]["n_trades"] < 100:
        warnings.append(
            "Удалено ≥3 модулей при <100 OOS-сделок: каждая итерация greedy — "
            "дополнительная степень свободы (multiple testing). Требуется "
            "walk-forward подтверждение рекомендованного набора.")
    if warnings:
        out["warnings"] = warnings
    return out
