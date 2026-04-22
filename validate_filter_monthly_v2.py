#!/usr/bin/env python3
"""
validate_filter_monthly_v2.py
-----------------------------
Side-by-side: F3 (CT & 15<=ADX<25) vs F7 (CT & ADX<25) pro Monat.
Zeigt welcher Filter praeziser arbeitet.
"""

import pandas as pd

KILLER_MONTHS = {"2024-06","2024-04","2024-02","2023-04","2023-08",
                 "2021-08","2021-10","2025-10","2026-01","2022-11","2022-12"}

REAL_KILLERS = {"2024-06","2024-04","2024-02","2023-04","2025-10","2026-01"}


def merge_flags(port, enriched):
    out = []
    for sym in port["symbol"].unique():
        p = port[port["symbol"] == sym].copy().sort_values("close_time").reset_index(drop=True)
        e = enriched[enriched["symbol"] == sym].copy().sort_values("entry_time").reset_index(drop=True)
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

    df["skip_F3"] = df["counter_trend"] & (df["adx14"] >= 15) & (df["adx14"] < 25)
    df["skip_F7"] = df["counter_trend"] & (df["adx14"] < 25)

    print("=" * 110)
    print(f"{'Monat':<10}{'n':>4}{'base':>9}{'F3_skip':>8}{'F3_R':>9}{'F3_dR':>9}"
          f"{'F7_skip':>8}{'F7_R':>9}{'F7_dR':>9}  TAG")
    print("-" * 110)

    sums = {"F3_killer":0.0,"F3_normal":0.0,"F7_killer":0.0,"F7_normal":0.0,
            "F3_real_killer":0.0,"F7_real_killer":0.0}

    for month, g in df.groupby("month"):
        n = len(g)
        base = g["R"].sum()
        f3_skip = int(g["skip_F3"].sum())
        f7_skip = int(g["skip_F7"].sum())
        f3_r = g.loc[~g["skip_F3"], "R"].sum()
        f7_r = g.loc[~g["skip_F7"], "R"].sum()
        f3_d, f7_d = f3_r - base, f7_r - base

        tag = ""
        if   month in REAL_KILLERS:  tag = "!!! REAL KILLER"
        elif month in KILLER_MONTHS: tag = "KILL (soft)"

        if month in KILLER_MONTHS:
            sums["F3_killer"] += f3_d
            sums["F7_killer"] += f7_d
        else:
            sums["F3_normal"] += f3_d
            sums["F7_normal"] += f7_d

        if month in REAL_KILLERS:
            sums["F3_real_killer"] += f3_d
            sums["F7_real_killer"] += f7_d

        print(f"{month:<10}{n:>4d}{base:>8.2f}R{f3_skip:>8d}{f3_r:>8.2f}R{f3_d:>+8.2f}R"
              f"{f7_skip:>8d}{f7_r:>8.2f}R{f7_d:>+8.2f}R  {tag}")

    print("-" * 110)
    print("\n" + "=" * 110)
    print("AGGREGAT")
    print("=" * 110)
    print(f"  ECHTE Killer (6 Monate): F3 {sums['F3_real_killer']:+.2f}R   "
          f"F7 {sums['F7_real_killer']:+.2f}R")
    print(f"  ALLE Killer  (11 M.)   : F3 {sums['F3_killer']:+.2f}R   "
          f"F7 {sums['F7_killer']:+.2f}R")
    print(f"  Normal-Monate (52)     : F3 {sums['F3_normal']:+.2f}R   "
          f"F7 {sums['F7_normal']:+.2f}R")
    print(f"  GESAMT                 : F3 {sums['F3_killer']+sums['F3_normal']:+.2f}R   "
          f"F7 {sums['F7_killer']+sums['F7_normal']:+.2f}R")

    # Monate wo F3 strikt besser als F7 ist
    per_m = df.groupby("month").apply(
        lambda g: pd.Series({
            "base":  g["R"].sum(),
            "F3_r":  g.loc[~g["skip_F3"], "R"].sum(),
            "F7_r":  g.loc[~g["skip_F7"], "R"].sum(),
        }), include_groups=False
    )
    per_m["F3_d"] = per_m["F3_r"] - per_m["base"]
    per_m["F7_d"] = per_m["F7_r"] - per_m["base"]
    per_m["F3_minus_F7"] = per_m["F3_d"] - per_m["F7_d"]

    print("\n" + "=" * 110)
    print("MONATE WO F3 BESSER ALS F7 (d.h. F7 killt Trades die er nicht killen sollte)")
    print("=" * 110)
    better = per_m[per_m["F3_minus_F7"] > 0.3].sort_values("F3_minus_F7", ascending=False)
    print(better[["base","F3_d","F7_d","F3_minus_F7"]].round(2).to_string() if len(better) else "  keine")

    print("\n" + "=" * 110)
    print("MONATE WO F7 BESSER ALS F3 (d.h. F3 verpasst Schutz)")
    print("=" * 110)
    worse  = per_m[per_m["F3_minus_F7"] < -0.3].sort_values("F3_minus_F7")
    print(worse[["base","F3_d","F7_d","F3_minus_F7"]].round(2).to_string()  if len(worse)  else "  keine")

    print("\n" + "=" * 110)
    print("VERDIKT")
    print("=" * 110)
    total_better_f3 = (per_m["F3_minus_F7"] > 0.1).sum()
    total_better_f7 = (per_m["F3_minus_F7"] < -0.1).sum()
    print(f"  Monate wo F3 besser: {total_better_f3}")
    print(f"  Monate wo F7 besser: {total_better_f7}")
    if sums['F3_killer']+sums['F3_normal'] > sums['F7_killer']+sums['F7_normal']:
        print(f"  -> F3 gewinnt im Aggregat")
    else:
        print(f"  -> F7 gewinnt im Aggregat")


if __name__ == "__main__":
    main()