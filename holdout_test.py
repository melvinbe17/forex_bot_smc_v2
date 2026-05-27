#!/usr/bin/env python3
"""
holdout_test.py
---------------
Out-of-Time-Holdout fuer das Regime-Overlay (Goldstandard gegen Overfitting).

Vorgehen:
  1. DESIGN-Fenster 2011-2019: Grid-Search ueber die Regime-Parameter,
     waehle die Kombi mit bester risikoadjustierter Kennzahl (MAR =
     Return/|MaxDD|) des Overlay-Portfolios.
  2. Parameter EINFRIEREN.
  3. HOLDOUT-Fenster 2020-2026 (nie fuer Design benutzt, inkl. COVID 2020):
     Baseline vs Overlay mit den eingefrorenen Parametern auswerten.

Haelt die Verbesserung im Holdout -> Edge generalisiert out-of-time.
Die Regelform (USDJPY beide Seiten, Carry|VIX & Proxy) ist die fixe Hypothese.

Usage:  python3 holdout_test.py
"""
from __future__ import annotations
import sys, itertools, warnings
import numpy as np
import pandas as pd
warnings.simplefilter("ignore")
sys.path.insert(0, ".")
import aggregate_multi as agg
import regime_overlay as ro

SYMS = ["EURUSD", "USDJPY", "XAUUSD"]
DESIGN = ("2000-01-01", "2020-01-01")    # 2011-2019
HOLDOUT = ("2020-01-01", "2030-01-01")   # 2020-2026

JPY = ro.daily_close("USDJPY"); XAU = ro.daily_close("XAUUSD")
MACRO = ro.load_macro()
US10 = ro._get(MACRO, "DGS10"); JP10 = ro._get(MACRO, *ro._JP10Y_ALIASES)
VIX = ro._get(MACRO, "VIXCLS", "VIX")
BASE = pd.concat([agg.load_trades(s, f"results/trades_{s}.csv") for s in SYMS]) \
         .sort_values("close_time").reset_index(drop=True)


def build_ro(sma_win, carry_win, vix_win, vix_mult):
    R = pd.DataFrame({"jpy": JPY, "xau": XAU}).dropna()
    proxy = (R["jpy"] < R["jpy"].rolling(sma_win).mean()) & \
            (R["xau"] > R["xau"].rolling(sma_win).mean())
    parts = []
    if US10 is not None and JP10 is not None:
        diff = US10.reindex(R.index, method="ffill") - JP10.reindex(R.index, method="ffill")
        parts.append(diff < diff.rolling(carry_win).mean())
    if VIX is not None:
        v = VIX.reindex(R.index, method="ffill")
        parts.append(v > v.rolling(vix_win).median() * vix_mult)
    macro_off = parts[0]
    for p in parts[1:]:
        macro_off = macro_off | p
    s = (macro_off.fillna(False) & proxy).shift(1).fillna(False).astype(bool)
    s.index = s.index.normalize()
    return s


def gate(trades, ro_daily):
    d = trades["close_time"].dt.normalize().map(ro_daily).fillna(False).astype(bool)
    return trades[~((trades["symbol"] == "USDJPY") & d)].copy()


def met(trades, a, b, risk=0.007):
    agg.RISK_PER_TRADE = risk
    m = (trades["close_time"] >= a) & (trades["close_time"] < b)
    t = trades[m].sort_values("close_time").reset_index(drop=True)
    r = agg.simulate_portfolio(t)
    gp = t.loc[t["R"] > 0, "R"].sum(); gl = -t.loc[t["R"] <= 0, "R"].sum()
    pf = gp/gl if gl > 0 else float("inf")
    return dict(ret=r["total_return_pct"], dd=r["max_dd_pct"], pf=pf,
                fd=len(r["daily_violations"]), ft=r["total_violated"], n=len(t))


# ---- 1) Grid-Search NUR auf Design-Fenster ----
gridvals = dict(sma_win=[150, 200, 250], carry_win=[60, 120, 180],
                vix_win=[60, 120, 180], vix_mult=[1.2, 1.3, 1.5])
combos = list(itertools.product(*gridvals.values()))
print(f"Grid-Search auf DESIGN 2011-2019: {len(combos)} Kombinationen ...")
best = None
for c in combos:
    params = dict(zip(gridvals.keys(), c))
    g = gate(BASE, build_ro(**params))
    m = met(g, *DESIGN)
    mar = m["ret"] / abs(m["dd"]) if m["dd"] != 0 else 0
    if best is None or mar > best[0]:
        best = (mar, params, m)
best_mar, best_params, best_design = best
base_design = met(BASE, *DESIGN)
print(f"\nDESIGN-Optimum (MAR={best_mar:.2f}): {best_params}")
print(f"  Baseline DESIGN : ret {base_design['ret']:.1f}%  dd {base_design['dd']:.2f}%  pf {base_design['pf']:.2f}")
print(f"  Overlay  DESIGN : ret {best_design['ret']:.1f}%  dd {best_design['dd']:.2f}%  pf {best_design['pf']:.2f}")

# ---- 2/3) EINGEFRORENE Parameter -> HOLDOUT 2020-2026 ----
frozen = build_ro(**best_params)
g_hold = gate(BASE, frozen)
print("\n" + "=" * 70)
print("  HOLDOUT 2020-2026  (eingefrorene Design-Parameter, nie fuer Design genutzt)")
print("=" * 70)
for risk in [0.007, 0.004]:
    bm = met(BASE, *HOLDOUT, risk=risk)
    om = met(g_hold, *HOLDOUT, risk=risk)
    print(f"\n  RISK {risk*100:.1f}%")
    print(f"    baseline : ret {bm['ret']:8.1f}%  dd {bm['dd']:7.2f}%  pf {bm['pf']:.2f}  FTMOd {bm['fd']}  tot {bm['ft']}  n {bm['n']}")
    print(f"    overlay  : ret {om['ret']:8.1f}%  dd {om['dd']:7.2f}%  pf {om['pf']:.2f}  FTMOd {om['fd']}  tot {om['ft']}  n {om['n']}")
    print(f"    delta    : ret {om['ret']-bm['ret']:+8.1f}   dd {om['dd']-bm['dd']:+7.2f}   pf {om['pf']-bm['pf']:+.2f}")

# Vergleich zum hand-gewaehlten Default
print("\n  Referenz: hand-gewaehlter Default (200,120,120,1.3) im HOLDOUT:")
gd = gate(BASE, build_ro(200, 120, 120, 1.3)); dm = met(gd, *HOLDOUT)
print(f"    overlay  : ret {dm['ret']:.1f}%  dd {dm['dd']:.2f}%  pf {dm['pf']:.2f}")
print(f"  -> Design-Optimum {best_params} vs Default: "
      f"{'praktisch gleich' if abs(best_params['sma_win']-200)<=50 else 'abweichend'}")
