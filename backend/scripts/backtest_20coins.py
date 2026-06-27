"""Бэктест 20 монет (синтетический контроль, та же логика/издержки).
Для реальных данных: заменить make_synthetic на client.klines_history +
client.funding_rate_history (см. backtester.run_backtest) и запустить
POST /api/v1/backtest/run по каждому символу."""
import sys, json, time
sys.path.insert(0, "/home/claude/crypto_signals_1h_top20/backend")
sys.path.insert(0, "/home/claude/crypto_signals/backend")  # генератор синтетики
from scripts.statistical_truth import make_synthetic
from scripts.cross_symbol_validation import synth_funding
sys.path.pop(0)
import numpy as np
from dataclasses import replace
from app.backtest.precompute import precompute_candidates
from app.backtest.portfolio import CostModel, simulate_portfolio

SYMS = ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","DOGEUSDT","ADAUSDT",
        "LINKUSDT","AVAXUSDT","TONUSDT","TRXUSDT","LTCUSDT","BCHUSDT","DOTUSDT",
        "NEARUSDT","APTUSDT","ATOMUSDT","INJUSDT","SEIUSDT","SUIUSDT"]
MONTHS, MS, MF = 24, 30.0, 3

rows = []
for i, sym in enumerate(SYMS):
    t0 = time.time()
    df = make_synthetic(sym, MONTHS, seed=500 + i*13)
    funding = synth_funding(df, 500 + i*13 + 1)
    cands = precompute_candidates(sym, "1h", df, progress_every=0, funding_df=funding)
    filtered = [c for c in cands if c.score >= MS and c.confirmations >= MF]
    res = simulate_portfolio(df, [replace(c) for c in filtered],
                             CostModel(funding_series=funding.set_index("fundingTime")["fundingRate"]))
    m = res.metrics
    hold = float(np.mean([t.bars_held for t in res.trades])) if res.trades else 0.0
    rows.append({"symbol": sym, "trades": m["n_trades"], "winrate": m["winrate"],
                 "pf": m["profit_factor"] if m["profit_factor"]!=float("inf") else 999.0,
                 "expectancy": m["expectancy_r"], "avg_hold_bars": round(hold,1),
                 "trades_r": [t.pnl_r for t in res.trades]})
    print(f"{sym}: n={m['n_trades']} WR={m['winrate']} PF={rows[-1]['pf']} "
          f"exp={m['expectancy_r']:+.4f} hold={hold:.0f}b ({time.time()-t0:.0f}s)", flush=True)
    json.dump(rows, open("/home/claude/bt20_partial.json","w"))

# агрегат
allr = np.array([r for x in rows for r in x["trades_r"]])
gw, gl = allr[allr>0].sum(), abs(allr[allr<=0].sum())
agg = {"trades": int(len(allr)), "winrate": round(float((allr>0).mean()*100),2),
       "pf": round(float(gw/gl),3) if gl>0 else 999.0,
       "expectancy": round(float(allr.mean()),4),
       "avg_hold_bars": round(float(np.mean([x["avg_hold_bars"] for x in rows])),1),
       "trades_per_symbol": round(len(allr)/len(SYMS),1)}
json.dump({"rows": rows, "aggregate": agg}, open("/home/claude/bt20_results.json","w"))
print("AGG:", json.dumps(agg))
