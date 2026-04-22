#!/usr/bin/env python3
"""
simulate_filter.py
-------------------
Simuliere Trend-basierte Filter auf der baseline 0.7% flat risk.

Merged die Portfolio-Chronologie (trades_portfolio.csv) mit den Trend-Features
aus dem killer_month_trend_analysis (killer_trend_enriched.csv).
Testet mehrere Filter-Varianten und zeigt Return + MaxDD + FTMO-safe.

Input:
  results/trades_portfolio.csv       (close_time, symbol, side, R, ...)
  results/killer_trend_enriched.csv  (entry_time, symbol, adx14, counter_trend, strong_ct)
"""

import argparse
from pathlib import Path
import numpy as np
import pandas as pd

START_BAL = 10000.0
RISK_PCT  = 0.007   # Baseline 0.7% flat
DAILY_LOSS_LIMIT = -0.05
TOTAL_LOSS_LIMIT = -0.10


def merge_flags(port: pd.DataFrame, enriched: pd.DataFrame) -> pd.DataFrame:
    """Portfolio chronologisch + Trend-Flags via per-Symbol Reihenfolge."""
    out = []
    for sym in port["symbol"].unique():
        p = port[port["symbol"] == sym].copy() \
            .sort_values("close_time").reset_index(drop=True)
        e = enriched[enriched["symbol"] == sym].copy() \
            .sort_values("entry_time").reset_index(drop=True)
        if len(p) != len(e):
            raise ValueError(f"{sym}: port={len(p)}  enriched={len(e)}  -- Mismatch")
        # R-Sanity check
        r_mismatch = (np.abs(p["R"].values - e["total_r"].values) > 0.01).sum()
        if r_mismatch > 0:
            print(f"  [WARN] {sym}: {r_mismatch} R-Werte weichen ab (Aggregations-Order?)")
        for col in ("entry_time", "direction", "adx14", "counter_trend", "strong_ct"):
            if col in e.columns:
                p[col] = e[col].values
        out.append(p)
    full = pd.concat(out, ignore_index=True) \
             .sort_values("close_time").reset_index(drop=True)
    return full


def simulate(df: pd.DataFrame, skip_mask: pd.Series) -> dict:
    balance = START_BAL
    peak    = balance
    max_dd  = 0.0
    taken, skipped = 0, 0
    day_start = balance
    last_day  = None
    daily_viol = 0
    total_hit  = False

    for idx, row in df.iterrows():
        d = pd.Timestamp(row["close_time"]).date()
        if last_day is None or d != last_day:
            day_start = balance
            last_day  = d
        if skip_mask.iloc[idx]:
            skipped += 1
            continue
        r    = float(row["R"])
        risk = balance * RISK_PCT
        pnl  = r * risk
        balance += pnl
        taken += 1

        if balance > peak:
            peak = balance
        dd = (balance / peak) - 1
        if dd < max_dd:
            max_dd = dd

        daily_ret = (balance / day_start) - 1
        if daily_ret <= DAILY_LOSS_LIMIT:
            daily_viol += 1
        if (balance / START_BAL) - 1 <= TOTAL_LOSS_LIMIT:
            total_hit = True

    return {
        "final":      balance,
        "return_pct": (balance / START_BAL - 1) * 100,
        "max_dd_pct": max_dd * 100,
        "taken":      taken,
        "skipped":    skipped,
        "daily_viol": daily_viol,
        "total_hit":  total_hit,
        "ftmo_safe":  (daily_viol == 0) and (not total_hit),
    }


def filter_baseline(df):      return pd.Series(False, index=df.index)
def filter_CT_15_20(df):      return df["counter_trend"] & (df["adx14"] >= 15) & (df["adx14"] < 20)
def filter_CT_15_22(df):      return df["counter_trend"] & (df["adx14"] >= 15) & (df["adx14"] < 22)
def filter_CT_15_25(df):      return df["counter_trend"] & (df["adx14"] >= 15) & (df["adx14"] < 25)
def filter_CT_12_22(df):      return df["counter_trend"] & (df["adx14"] >= 12) & (df["adx14"] < 22)
def filter_CT_10_25(df):      return df["counter_trend"] & (df["adx14"] >= 10) & (df["adx14"] < 25)
def filter_all_strong_CT(df): return df["strong_ct"].astype(bool)
def filter_CT_under_25(df):   return df["counter_trend"] & (df["adx14"] < 25)
def filter_CT_under_20(df):   return df["counter_trend"] & (df["adx14"] < 20)

FILTERS = {
    "baseline_0.7pct":        filter_baseline,
    "F1_CT_adx_15_20":        filter_CT_15_20,
    "F2_CT_adx_15_22":        filter_CT_15_22,
    "F3_CT_adx_15_25":        filter_CT_15_25,
    "F4_CT_adx_12_22":        filter_CT_12_22,
    "F5_CT_adx_10_25":        filter_CT_10_25,
    "F6_all_strong_CT":       filter_all_strong_CT,
    "F7_CT_adx_under_25":     filter_CT_under_25,
    "F8_CT_adx_under_20":     filter_CT_under_20,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port",     default="results/trades_portfolio.csv")
    ap.add_argument("--enriched", default="results/killer_trend_enriched.csv")
    args = ap.parse_args()

    port = pd.read_csv(args.port)
    port["close_time"] = pd.to_datetime(port["close_time"])
    enriched = pd.read_csv(args.enriched)
    enriched["entry_time"] = pd.to_datetime(enriched["entry_time"])

    print(f"Portfolio trades: {len(port)}   Enriched: {len(enriched)}")

    df = merge_flags(port, enriched)
    print(f"Merged: {len(df)}  (range {df['close_time'].min()} -> {df['close_time'].max()})")

    # Filter-Statistiken
    print("\nFilter-Statistiken (wie viele Trades wuerden geskippt):")
    for name, fn in FILTERS.items():
        mask = fn(df)
        print(f"  {name:<24} skip={mask.sum():4d} / {len(df)}  ({100*mask.sum()/len(df):5.1f}%)")

    print("\n" + "=" * 98)
    print(f"{'Filter':<24}{'Return':>10}{'MaxDD':>10}{'Taken':>8}{'Skip':>7}"
          f"{'D-Viol':>8}{'FTMO-safe':>12}")
    print("=" * 98)
    results = {}
    for name, fn in FILTERS.items():
        mask = fn(df)
        res  = simulate(df, mask)
        results[name] = res
        safe = "JA" if res["ftmo_safe"] else "NEIN"
        print(f"{name:<24}{res['return_pct']:>9.2f}%{res['max_dd_pct']:>9.2f}%"
              f"{res['taken']:>8d}{res['skipped']:>7d}{res['daily_viol']:>8d}"
              f"{safe:>12}")
    print("=" * 98)

    # Delta vs baseline
    base = results["baseline_0.7pct"]
    print(f"\nDELTA vs baseline ({base['return_pct']:.2f}% / DD {base['max_dd_pct']:.2f}%)")
    print("=" * 98)
    for name in FILTERS:
        if name == "baseline_0.7pct":
            continue
        r = results[name]
        d_ret = r["return_pct"] - base["return_pct"]
        d_dd  = r["max_dd_pct"] - base["max_dd_pct"]
        safe  = "JA" if r["ftmo_safe"] else "NEIN"
        # Efficiency: wie viel Return pro pp DD-Reduktion
        eff   = d_ret / d_dd if abs(d_dd) > 0.01 else float("nan")
        print(f"  {name:<24}  dReturn {d_ret:+7.2f}pp   dDD {d_dd:+6.2f}pp   "
              f"Eff(R/DD) {eff:+6.2f}   FTMO:{safe}")
    print("=" * 98)

    # Highlights
    print("\nGewinner:")
    safe_ones = {n: r for n, r in results.items() if r["ftmo_safe"] and n != "baseline_0.7pct"}
    if safe_ones:
        best = max(safe_ones.items(), key=lambda kv: kv[1]["return_pct"])
        print(f"  Beste FTMO-safe Variante:   {best[0]}   "
              f"Return {best[1]['return_pct']:.2f}%   DD {best[1]['max_dd_pct']:.2f}%")
    else:
        print("  Keine Variante ist FTMO-safe")
    best_ret = max(results.items(), key=lambda kv: kv[1]["return_pct"])
    print(f"  Bester Return insgesamt:     {best_ret[0]}   "
          f"Return {best_ret[1]['return_pct']:.2f}%   DD {best_ret[1]['max_dd_pct']:.2f}%")
    least_dd = max(results.items(), key=lambda kv: kv[1]["max_dd_pct"])
    print(f"  Geringster DD:               {least_dd[0]}   "
          f"Return {least_dd[1]['return_pct']:.2f}%   DD {least_dd[1]['max_dd_pct']:.2f}%")


if __name__ == "__main__":
    main()