import pandas as pd, numpy as np, sys, time, warnings
warnings.simplefilter("ignore")
sys.path.insert(0, ".")
import config
config.REGIME_OVERLAY_ENABLED = False   # wir filtern manuell -> find nur 1x
from data_loader import load_symbol, resample
from smc_patterns import analyze
from smc_strategy import find_all_setups
import backtest_m15 as bt
import aggregate_multi as agg
import regime_overlay as ro

t0 = time.time()
print("Lade USDJPY 14J ...", flush=True)
ltf = load_symbol("USDJPY", timeframes=["M15"], source="parquet")["M15"]
print(f"  {len(ltf)} Bars ({ltf.index[0]} -> {ltf.index[-1]})  [{time.time()-t0:.0f}s]", flush=True)
htf = resample(ltf, "H1")
htf_snap = analyze(htf); ltf_snap = analyze(ltf)
print(f"Setup-Finder laeuft ... [{time.time()-t0:.0f}s]", flush=True)
setups = find_all_setups(ltf, ltf_snap, htf_snap, htf_df=htf, symbol="USDJPY")
print(f"  Setups gesamt: {len(setups)}  [{time.time()-t0:.0f}s]", flush=True)

# Regime-Filter auf Setups (beide Seiten im Risk-Off)
reg = ro.build_regime()
ros = reg["risk_off_lag"].copy(); ros.index = ros.index.normalize()
def keep(s):
    return not bool(ros.get(s.entry_time.normalize(), False))
setups_ovl = [s for s in setups if keep(s)]
print(f"  Setups nach Overlay: {len(setups_ovl)}  (gegated {len(setups)-len(setups_ovl)})", flush=True)

tb, _, _ = bt.simulate(ltf, setups)
to, _, _ = bt.simulate(ltf, setups_ovl)
pd.DataFrame([t.summary_row() for t in tb]).to_csv("results/trades_USDJPY_base.csv", index=False)
pd.DataFrame([t.summary_row() for t in to]).to_csv("results/trades_USDJPY_ovl.csv", index=False)
print(f"  USDJPY Trades: baseline={len(tb)}  overlay={len(to)}  [{time.time()-t0:.0f}s]", flush=True)

# ---- Portfolio-Vergleich (engine-faithful USDJPY + bestehende EUR/XAU) ----
def load_portfolio(jpy_path):
    parts = [agg.load_trades("EURUSD", "results/trades_EURUSD.csv"),
             agg.load_trades("XAUUSD", "results/trades_XAUUSD.csv"),
             agg.load_trades("USDJPY", jpy_path)]
    return pd.concat(parts).sort_values("close_time").reset_index(drop=True)

base = load_portfolio("results/trades_USDJPY_base.csv")
ovl  = load_portfolio("results/trades_USDJPY_ovl.csv")

def met(t, risk, a, b):
    agg.RISK_PER_TRADE = risk
    m = (t["close_time"] >= a) & (t["close_time"] < b)
    t = t[m].sort_values("close_time").reset_index(drop=True)
    r = agg.simulate_portfolio(t)
    gp = t.loc[t["R"] > 0, "R"].sum(); gl = -t.loc[t["R"] <= 0, "R"].sum()
    pf = gp/gl if gl > 0 else float("inf")
    return r["total_return_pct"], r["max_dd_pct"], pf, len(r["daily_violations"]), r["total_violated"], len(t)

W = [("FULL 2011-2026","2000","2030"),("IS 2021-2026","2021","2030"),("OOS 2011-2020","2000","2021")]
print("\n==== ENGINE-FAITHFUL PORTFOLIO (USDJPY beide Seiten, Risk-Off-Gate) ====", flush=True)
for risk in [0.007, 0.004]:
    print(f"\n#### RISK {risk*100:.1f}% ####")
    for lbl,a,b in W:
        br = met(base,risk,a,b); orr = met(ovl,risk,a,b)
        print(f"  [{lbl}]")
        print(f"    baseline  ret {br[0]:8.1f}  dd {br[1]:7.2f}  pf {br[2]:.2f}  FTMOd {br[3]}  tot {br[4]}  n {br[5]}")
        print(f"    overlay   ret {orr[0]:8.1f}  dd {orr[1]:7.2f}  pf {orr[2]:.2f}  FTMOd {orr[3]}  tot {orr[4]}  n {orr[5]}")
        print(f"    delta     ret {orr[0]-br[0]:+8.1f}  dd {orr[1]-br[1]:+7.2f}")
print(f"\nDONE [{time.time()-t0:.0f}s]", flush=True)
