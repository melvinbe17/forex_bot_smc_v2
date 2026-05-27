#!/usr/bin/env python3
"""
eval_overlay.py
---------------
Validiert das Regime-Overlay (regime_overlay.py) gegen die v0.6-Baseline.

Fuer jede Kombination aus Risk-Stufe und Zeitfenster (14J full / In-Sample
2021+ / OOS 2011-2020) wird das Portfolio mit und ohne Gate durch dieselbe
FTMO-Engine (aggregate_multi.simulate_portfolio) gerechnet:

    Rendite %, MaxDD %, PF, FTMO Daily/Total-Verstoesse, Trades.

So ist die Pruefung methodisch sauber: identische Engine, nur Trade-Set
unterscheidet sich. Verbesserung muss IN-SAMPLE *und* OOS halten.

Usage:
    python3 eval_overlay.py
"""
from __future__ import annotations

import sys
import pandas as pd

sys.path.insert(0, ".")
import aggregate_multi as agg
import regime_overlay as ro

SYMS = ["EURUSD", "USDJPY", "XAUUSD"]
RISKS = [0.007, 0.004]
WINDOWS = [
    ("FULL 2011-2026", "2000-01-01", "2030-01-01"),
    ("IS   2021-2026", "2021-01-01", "2030-01-01"),
    ("OOS  2011-2020", "2000-01-01", "2021-01-01"),
]


def load_all() -> pd.DataFrame:
    parts = [agg.load_trades(s, f"results/trades_{s}.csv") for s in SYMS]
    parts = [p for p in parts if p is not None]
    return pd.concat(parts).sort_values("close_time").reset_index(drop=True)


def metrics(trades: pd.DataFrame, risk: float) -> dict:
    agg.RISK_PER_TRADE = risk
    t = trades.sort_values("close_time").reset_index(drop=True)
    r = agg.simulate_portfolio(t)
    gp = t.loc[t["R"] > 0, "R"].sum()
    gl = -t.loc[t["R"] <= 0, "R"].sum()
    pf = gp / gl if gl > 0 else float("inf")
    return dict(ret=r["total_return_pct"], dd=r["max_dd_pct"], pf=pf,
                fd=len(r["daily_violations"]), ft=r["total_violated"], n=len(t))


def window(t: pd.DataFrame, a: str, b: str) -> pd.DataFrame:
    m = (t["close_time"] >= a) & (t["close_time"] < b)
    return t[m].copy()


def main():
    base = load_all()
    regime = ro.build_regime()
    # Validierte Default-Regel: USDJPY BEIDE Seiten im Risk-Off-Regime aussetzen
    # (Carry faellt ODER VIX-Spike, durch Intermarket-Proxy bestaetigt).
    gated = ro.apply_gate(base, regime,
                          gate_symbols=("USDJPY",), gate_sides=None)

    src = "ECHTE MAKRODATEN (data/macro/)" if regime.attrs["has_macro"] \
        else "INTERNER PREIS-PROXY (keine Makrodaten gefunden)"
    print("=" * 74)
    print(f"  REGIME-OVERLAY VALIDIERUNG   |   Datenquelle: {src}")
    print(f"  Gate: USDJPY (beide Seiten) im Risk-Off-Regime aussetzen")
    print(f"  Baseline {len(base)} Trades  ->  Gated {len(gated)} "
          f"(entfernt {len(base)-len(gated)})")
    print("=" * 74)

    rows = []
    for risk in RISKS:
        print(f"\n################  RISK {risk*100:.1f}%  ################")
        for label, a, b in WINDOWS:
            bm = metrics(window(base, a, b), risk)
            gm = metrics(window(gated, a, b), risk)
            print(f"\n  [{label}]")
            hdr = f"    {'':9}{'ret%':>9}{'MaxDD%':>9}{'PF':>7}{'FTMOd':>7}{'FTMOtot':>9}{'trades':>8}"
            print(hdr)
            print(f"    {'baseline':9}{bm['ret']:9.1f}{bm['dd']:9.2f}{bm['pf']:7.2f}"
                  f"{bm['fd']:7d}{str(bm['ft']):>9}{bm['n']:8d}")
            print(f"    {'overlay':9}{gm['ret']:9.1f}{gm['dd']:9.2f}{gm['pf']:7.2f}"
                  f"{gm['fd']:7d}{str(gm['ft']):>9}{gm['n']:8d}")
            d_ret, d_dd = gm['ret'] - bm['ret'], gm['dd'] - bm['dd']
            print(f"    {'Δ':9}{d_ret:+9.1f}{d_dd:+9.2f}{gm['pf']-bm['pf']:+7.2f}")
            for tag, m in [("baseline", bm), ("overlay", gm)]:
                rows.append(dict(risk=risk, window=label.strip(), variant=tag, **m))

    out = pd.DataFrame(rows)
    out.to_csv("results/overlay_validation.csv", index=False)
    print(f"\nGespeichert: results/overlay_validation.csv")


if __name__ == "__main__":
    main()
