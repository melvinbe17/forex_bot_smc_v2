"""
analyze_trades.py  (forex_bot_smc)
==================================

Breakdown der bt_trades.csv nach Jahr / Quartal / Richtung / Zone.
Zeigt: n, WR, avgR, sumR, ProfitFactor, MaxDD (in R-Einheiten).

Diagnostisch fuer die Frage: war 2021 eine Outlier-Phase die den Backtest
schoen aussehen laesst, waehrend 2022ff. die Strategie negativ ist?

Usage:
    python3 analyze_trades.py                       # default: results/bt_trades.csv
    python3 analyze_trades.py path/to/bt_trades.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------
def load_trades(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "entry_time" not in df.columns:
        raise RuntimeError(
            f"'entry_time' fehlt in {path}. Columns: {list(df.columns)}"
        )
    if "total_r" not in df.columns:
        raise RuntimeError(
            f"'total_r' fehlt in {path}. Columns: {list(df.columns)}"
        )
    df["entry_time"] = pd.to_datetime(df["entry_time"])
    df = df.sort_values("entry_time").reset_index(drop=True)
    df["year"] = df["entry_time"].dt.year
    df["quarter"] = df["entry_time"].dt.to_period("Q").astype(str)
    return df


# ---------------------------------------------------------------------------
# Statistik
# ---------------------------------------------------------------------------
def stats(group: pd.DataFrame, label: str) -> dict:
    r = group["total_r"].to_numpy()
    n = len(r)
    if n == 0:
        return {"period": label, "n": 0, "wins": 0, "losses": 0,
                "wr_pct": 0.0, "avg_r": 0.0, "sum_r": 0.0,
                "pf": 0.0, "max_dd_r": 0.0}

    wins_mask = r > 0
    loss_mask = r < 0
    nw = int(wins_mask.sum())
    nl = int(loss_mask.sum())
    sum_wins = float(r[wins_mask].sum())
    sum_losses = float(-r[loss_mask].sum())
    pf = sum_wins / sum_losses if sum_losses > 0 else float("inf")

    # MaxDD in R-Einheiten (peak-to-trough der cum-sum)
    equity = r.cumsum()
    peak = np.maximum.accumulate(equity)
    dd = equity - peak
    max_dd_r = float(dd.min()) if len(dd) else 0.0

    return {
        "period": label,
        "n": n,
        "wins": nw,
        "losses": nl,
        "wr_pct": 100.0 * nw / n,
        "avg_r": float(r.mean()),
        "sum_r": float(r.sum()),
        "pf": pf,
        "max_dd_r": max_dd_r,
    }


def print_table(rows: list[dict]) -> None:
    hdr = (f"{'Periode':<10} {'n':>4} {'W':>4} {'L':>4} {'WR%':>6} "
           f"{'avgR':>7} {'sumR':>8} {'PF':>6} {'DD_R':>8}")
    print(hdr)
    print("-" * len(hdr))
    for row in rows:
        pf_str = f"{row['pf']:>6.2f}" if row['pf'] != float("inf") else "   inf"
        print(
            f"{row['period']:<10} "
            f"{row['n']:>4d} {row['wins']:>4d} {row['losses']:>4d} "
            f"{row['wr_pct']:>6.1f} "
            f"{row['avg_r']:>+7.3f} "
            f"{row['sum_r']:>+8.2f} "
            f"{pf_str} "
            f"{row['max_dd_r']:>+8.2f}"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("results/bt_trades.csv")
    if not path.exists():
        print(f"FEHLER: {path} nicht gefunden. "
              f"Hast du den Backtest schon gelaufen?")
        sys.exit(1)

    df = load_trades(path)
    print(f"Geladen: {len(df)} Trades "
          f"({df['entry_time'].min()} -> {df['entry_time'].max()})")
    print(f"Gesamt-R: {df['total_r'].sum():+.2f}  "
          f"WR: {100*(df['total_r']>0).mean():.1f}%")
    print()

    # Per-Year
    print("=" * 72)
    print("BREAKDOWN NACH JAHR")
    print("=" * 72)
    rows = [stats(g, str(y)) for y, g in df.groupby("year")]
    rows.append(stats(df, "GESAMT"))
    print_table(rows)

    # Per-Quarter (nur wenn >4 Jahre Daten)
    print()
    print("=" * 72)
    print("BREAKDOWN NACH QUARTAL")
    print("=" * 72)
    rows = [stats(g, q) for q, g in df.groupby("quarter")]
    print_table(rows)

    # Per-Direction
    print()
    print("=" * 72)
    print("BREAKDOWN NACH RICHTUNG")
    print("=" * 72)
    if "direction" in df.columns:
        rows = [stats(df[df["direction"] == d], d)
                for d in sorted(df["direction"].unique())]
        rows.append(stats(df, "GESAMT"))
        print_table(rows)

    # Per-Zone
    print()
    print("=" * 72)
    print("BREAKDOWN NACH ZONE (OB vs FVG)")
    print("=" * 72)
    if "zone_kind" in df.columns:
        rows = [stats(df[df["zone_kind"] == z], str(z))
                for z in sorted(df["zone_kind"].unique())]
        rows.append(stats(df, "GESAMT"))
        print_table(rows)

    # Jahr x Richtung  (die interessante Matrix)
    print()
    print("=" * 72)
    print("JAHR x RICHTUNG")
    print("=" * 72)
    rows = []
    for year, yg in df.groupby("year"):
        for d in sorted(yg["direction"].unique()):
            rows.append(stats(yg[yg["direction"] == d], f"{year} {d}"))
    print_table(rows)

    print()
    print("=" * 72)
    print("INTERPRETATIONS-HINWEIS")
    print("=" * 72)
    print("  - sumR = kumulierte R-Multiples fuer das Jahr/Quartal.")
    print("  - PF < 1.0 = Verlustperiode. PF > 1.5 = solide.")
    print("  - DD_R = groesster Drawdown in R innerhalb der Periode.")
    print("  - Wenn 2021 deutlich besser ist als 2022-2026: Overfitting")
    print("    auf ein guenstiges Regime. Ein Trendfilter (D1-Bias) oder")
    print("    Regime-Detection (ADX / ATR-Expansion) sollte 2022 raushalten.")


if __name__ == "__main__":
    main()
