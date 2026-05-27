#!/usr/bin/env python3
"""
walk_forward.py
---------------
Robustheits-Gate fuer das Regime-Overlay (Strategie 2).

Zwei Tests:
  (A) Parameter-Robustheit (one-at-a-time): jeden Regime-Parameter um den
      Default variieren. Bleibt die 14J/OOS-Verbesserung stabil -> Plateau
      (robust). Nur ein Punkt funktioniert -> Messerschneide (uebertuned).
  (B) Zeitliche Konsistenz / Walk-Forward: 14J in 2-Jahres-Bloecke teilen,
      Baseline vs Overlay je Block. Verbesserung muss ueber die MEISTEN
      Bloecke halten, nicht von einem getrieben sein.

Das Regime-Signal nutzt nur trailing Statistiken (rollierender Schnitt,
fester VIX-Multiplikator) -> lookahead-frei per Konstruktion. Hier geht es
um die Robustheit der REGELFORM.

Schnell: Post-hoc-Gate auf den Trade-Logs (in 2019 als pfadgenau bestaetigt).
Usage:  python3 walk_forward.py
"""
from __future__ import annotations
import sys, warnings
import numpy as np
import pandas as pd
warnings.simplefilter("ignore")
sys.path.insert(0, ".")
import aggregate_multi as agg
import regime_overlay as ro

SYMS = ["EURUSD", "USDJPY", "XAUUSD"]
DEFAULT = dict(sma_win=200, carry_win=120, vix_win=120, vix_mult=1.3)

# ---- Daten einmal cachen ----
JPY = ro.daily_close("USDJPY")
XAU = ro.daily_close("XAUUSD")
MACRO = ro.load_macro()
US10 = ro._get(MACRO, "DGS10")
JP10 = ro._get(MACRO, *ro._JP10Y_ALIASES)
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
    if parts:
        macro_off = parts[0]
        for p in parts[1:]:
            macro_off = macro_off | p
        risk_off = macro_off.fillna(False) & proxy
    else:
        risk_off = proxy
    s = risk_off.shift(1).fillna(False).astype(bool)
    s.index = s.index.normalize()
    return s


def gate(trades, ro_daily):
    d = trades["close_time"].dt.normalize().map(ro_daily).fillna(False).astype(bool)
    drop = (trades["symbol"] == "USDJPY") & d   # beide Seiten
    return trades[~drop].copy()


def met(trades, risk, a="2000", b="2030"):
    agg.RISK_PER_TRADE = risk
    m = (trades["close_time"] >= a) & (trades["close_time"] < b)
    t = trades[m].sort_values("close_time").reset_index(drop=True)
    if len(t) == 0:
        return (0.0, 0.0, 0.0, 0)
    r = agg.simulate_portfolio(t)
    gp = t.loc[t["R"] > 0, "R"].sum(); gl = -t.loc[t["R"] <= 0, "R"].sum()
    pf = gp/gl if gl > 0 else float("inf")
    return (r["total_return_pct"], r["max_dd_pct"], pf, len(t))


def run_variant(params, a="2000", b="2030"):
    rod = build_ro(**params)
    g = gate(BASE, rod)
    return met(BASE, 0.007, a, b), met(g, 0.007, a, b)


# ===================== (A) Parameter-Robustheit =====================
print("=" * 84)
print("  (A) PARAMETER-ROBUSTHEIT (one-at-a-time, Portfolio 0.7%)")
print("=" * 84)
print(f"  {'Parameter':22}{'FULL ret':>9}{'FULL dd':>9}{'OOS ret':>9}{'OOS dd':>9}{'gated':>7}")
b_full = met(BASE, 0.007); b_oos = met(BASE, 0.007, "2000", "2021")
print(f"  {'BASELINE (kein Gate)':22}{b_full[0]:9.1f}{b_full[1]:9.2f}{b_oos[0]:9.1f}{b_oos[1]:9.2f}{0:7d}")
print("  " + "-" * 70)

grid = [("default", DEFAULT)]
for v in [100, 150, 250, 300]:
    grid.append((f"sma_win={v}", {**DEFAULT, "sma_win": v}))
for v in [60, 90, 180]:
    grid.append((f"carry_win={v}", {**DEFAULT, "carry_win": v}))
for v in [60, 90, 180]:
    grid.append((f"vix_win={v}", {**DEFAULT, "vix_win": v}))
for v in [1.15, 1.2, 1.5, 1.7]:
    grid.append((f"vix_mult={v}", {**DEFAULT, "vix_mult": v}))

for name, params in grid:
    full = met(gate(BASE, build_ro(**params)), 0.007)
    oos = met(gate(BASE, build_ro(**params)), 0.007, "2000", "2021")
    ng = len(BASE) - len(gate(BASE, build_ro(**params)))
    mark = "  <-- default" if name == "default" else ""
    print(f"  {name:22}{full[0]:9.1f}{full[1]:9.2f}{oos[0]:9.1f}{oos[1]:9.2f}{ng:7d}{mark}")

# ===================== (B) Zeitliche Konsistenz =====================
print("\n" + "=" * 84)
print("  (B) WALK-FORWARD / ZEITLICHE KONSISTENZ (Default-Parameter, 2-Jahres-Bloecke)")
print("=" * 84)
print(f"  {'Block':14}{'base ret':>9}{'base dd':>9}{'ovl ret':>9}{'ovl dd':>9}{'Δret':>8}{'Δdd':>8}")
rod = build_ro(**DEFAULT)
g = gate(BASE, rod)
blocks = [("2011-2012","2011","2013"),("2013-2014","2013","2015"),
          ("2015-2016","2015","2017"),("2017-2018","2017","2019"),
          ("2019-2020","2019","2021"),("2021-2022","2021","2023"),
          ("2023-2024","2023","2025"),("2025-2026","2025","2027")]
improved = 0
for lbl, a, b in blocks:
    bm = met(BASE, 0.007, a, b); om = met(g, 0.007, a, b)
    dret, ddd = om[0]-bm[0], om[1]-bm[1]
    if ddd >= -0.01:  # DD nicht schlechter
        improved += 1
    print(f"  {lbl:14}{bm[0]:9.1f}{bm[1]:9.2f}{om[0]:9.1f}{om[1]:9.2f}{dret:+8.1f}{ddd:+8.2f}")
print("  " + "-" * 70)
print(f"  DD verbessert/gehalten in {improved}/{len(blocks)} Bloecken")
