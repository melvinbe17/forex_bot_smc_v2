#!/usr/bin/env python3
"""
validate_filter_monthly.py
--------------------------
Per-Monat Breakdown fuer F7 (CT & ADX<25) vs Baseline.
Zeigt: hilft der Filter wirklich in JEDEM Killer-Monat, oder
       nur im Aggregat (Simpson's Paradox Check).

Input:
  results/trades_portfolio.csv
  results/killer_trend_enriched.csv

Output: Tabelle (Monat / n / baseline R / F7 R / delta R / skipped)
"""

import numpy as np
import pandas as pd

KILLER_MONTHS = {"2024-06","2024-04","2024-02","2023-04","2023-08",
                 "2021-08","2021-10","2025-10","2026-01","2022-11","2022-12"}


def merge_flags(port, enriched):
    out = []
    for sym in port["symbol"].unique():
        p = port[port["symbol"] == sym].copy().sort_values("close_time").reset_index(drop=True)
        e = enriched[enriched["symbol"] == sym].copy().sort_values("entry_time").reset_index(drop=True)
        if len(p) != len(e):
            raise ValueError(f"{sym}: {len(p)} vs {len(e)}")
        for col in ("entry_time","direction","adx14","counter_trend","strong_ct"):
            if col in e.columns:
                p[col] = e[col].values
        out.append(p)
    return pd.concat(out, ignore_index=True).sort_values("close_time").reset_index(drop=True)


def main():
    port = pd.read_csv("results/trades_portfolio.csv")
    port["close_time"] = pd.to_datetime(port["close_time"])
    enriched = pd.read_csv("results/killer_trend_enriched.csv")
    enriched["entry_time"] = pd.to_datetime(enriched["entry_time"])

    df = merge_flags(port, enriched)
    df["month"] = df["close_time"].dt.to_period("M").astype(str)

    # F7 mask: CT & ADX<25
    df["skip_f7"] = df["counter_trend"] & (df["adx14"] < 25)

    print("=" * 100)
    print("PER-MONAT BREAKDOWN: Baseline vs F7 (skip CT & ADX<25)")
    print("=" * 100)
    print(f"{'Monat':<10}{'n':>5}{'WR%':>7}{'sumR_base':>12}"
          f"{'skip':>6}{'sumR_F7':>11}{'dR':>9}  KILLER?")
    print("-" * 100)

    killer_dR, normal_dR = 0.0, 0.0
    killer_n_skip, normal_n_skip = 0, 0

    for month, g in df.groupby("month"):
        n = len(g)
        wins = int((g["R"] > 0).sum())
        wr = 100.0 * wins / n if n else 0
        sumR_base = g["R"].sum()
        skip = int(g["skip_f7"].sum())
        sumR_f7 = g.loc[~g["skip_f7"], "R"].sum()
        dR = sumR_f7 - sumR_base
        is_killer = "KILL" if month in KILLER_MONTHS else ""
        marker = ""
        if month in KILLER_MONTHS:
            killer_dR += dR
            killer_n_skip += skip
        else:
            normal_dR += dR
            normal_n_skip += skip
            if dR < -0.5:  # Filter schadet im Normal-Monat
                marker = " <-- FILTER SCHADET"
        print(f"{month:<10}{n:>5d}{wr:>6.1f}%{sumR_base:>11.2f}R"
              f"{skip:>6d}{sumR_f7:>10.2f}R{dR:>+8.2f}R  {is_killer}{marker}")

    print("-" * 100)
    print(f"\nKILLER-Monate   : dR = {killer_dR:+.2f}R  (Skips: {killer_n_skip})")
    print(f"NORMAL-Monate   : dR = {normal_dR:+.2f}R  (Skips: {normal_n_skip})")
    print(f"GESAMT          : dR = {killer_dR+normal_dR:+.2f}R")

    # Worst normal months (where filter hurts most)
    per_month = df.groupby("month").apply(
        lambda g: pd.Series({
            "n": len(g),
            "sumR_base": g["R"].sum(),
            "sumR_f7": g.loc[~g["skip_f7"], "R"].sum(),
            "skip": int(g["skip_f7"].sum()),
            "is_killer": g.name in KILLER_MONTHS,
        })
    , include_groups=False)
    per_month["dR"] = per_month["sumR_f7"] - per_month["sumR_base"]

    print("\n" + "=" * 100)
    print("SCHADEN: Wo kostet der Filter am meisten R (Normal-Monate)?")
    print("=" * 100)
    harm = per_month[(~per_month["is_killer"].astype(bool)) & (per_month["dR"] < 0)] \
           .sort_values("dR").head(10)
    if len(harm):
        print(harm[["n","sumR_base","sumR_f7","skip","dR"]].round(2).to_string())
    else:
        print("  Kein Normal-Monat wo Filter schadet (Filter ist robust)")

    print("\n" + "=" * 100)
    print("HILFE: Wo hilft der Filter am meisten R (Killer-Monate)?")
    print("=" * 100)
    help_df = per_month[per_month["is_killer"].astype(bool)] \
              .sort_values("dR", ascending=False)
    print(help_df[["n","sumR_base","sumR_f7","skip","dR"]].round(2).to_string())


if __name__ == "__main__":
    main()