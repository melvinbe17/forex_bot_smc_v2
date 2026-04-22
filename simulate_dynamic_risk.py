#!/usr/bin/env python3
"""
simulate_dynamic_risk.py
------------------------
Re-simuliert die Portfolio-Equity mit verschiedenen Dynamic-Risk-Staffelungen.
Nimmt das bestehende trades_portfolio.csv (Trade-Signale & R) als Input und
sized jeden Trade neu basierend auf der aktuellen Drawdown-Tiefe.

Key Idea: Trade-Signale bleiben gleich, nur das Position-Sizing wird
dynamisch. Wenn Account -3% unten ist, halbe Size. Bei -5%, Viertel-Size.
Bei -7%, Pause bis Recovery.

Die Attribution hat gezeigt: alle grossen DD-Phasen werden von 1-2
Killer-Monaten getrieben. Dynamic Risk bremst genau diese Monate
mathematisch ein.

Usage:
    python3 simulate_dynamic_risk.py
    python3 simulate_dynamic_risk.py --plot
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Tuple

import pandas as pd


DEFAULT_INPUT = "results/trades_portfolio.csv"
DEFAULT_START = 10_000.0


# ----------------------------------------------------------------------
# SCHEDULES — so spricht die Regel-Tabelle:
#   (dd_threshold, risk_fraction)
#   dd=-0.04 (-4% DD) mit Stufe_A: trifft -0.03-Threshold, Risk=0.4%
#   dd=-0.08  trifft -0.07, Risk=0.0 = PAUSE
# ----------------------------------------------------------------------

# In simulate_dynamic_risk.py, SCHEDULES ersetzen durch:

SCHEDULES = {
    "baseline_0.7pct_flat":          [(0.00, 0.007)],
    "stufe_E_flat_0.4pct":           [(0.00, 0.004)],
    "stufe_D_flat_0.5pct":           [(0.00, 0.005)],
    # Neue: throttle nur in tiefem DD
    "stufe_J_late_throttle": [
        (-0.07, 0.0025),   # unter -7%: 0.25%
        (-0.04, 0.0045),   # -4% bis -7%: 0.45%
        ( 0.00, 0.0070),   # 0 bis -4%: volle 0.7%
    ],
    "stufe_K_very_late_throttle": [
        (-0.08, 0.0020),
        (-0.05, 0.0040),
        ( 0.00, 0.0070),
    ],
    "stufe_L_late_gentle": [
        (-0.06, 0.0040),   # unter -6%: 0.4%
        ( 0.00, 0.0070),   # sonst volle 0.7%
    ],
    # Bestes von vorher zum Vergleich
    "stufe_I_asymmetric": [
        (-0.08, 0.0010),
        (-0.05, 0.0035),
        ( 0.00, 0.0070),
    ],
}


def get_risk(dd: float, schedule: List[Tuple[float, float]]) -> float:
    """
    Gibt Risk-Fraction fuer Current-DD zurueck. 0.0 = Pause.
    Schedule wird ascending nach threshold sortiert und vom schlimmsten
    bis zum besten durchlaufen.
    """
    sched = sorted(schedule)
    for threshold, risk in sched:
        if dd <= threshold:
            return risk
    return sched[-1][1]


def simulate(df: pd.DataFrame, start: float, schedule,
             hysteresis_recover: float = 0.02):
    """
    Simuliert Equity-Kurve mit Dynamic Risk.

    hysteresis_recover: wenn einmal im Pause-Mode (Risk=0), bleibe drin
    bis DD besser als -hysteresis_recover. Verhindert Flip-Flop an der
    Pause-Schwelle.
    """
    bal = start
    running_max = start
    paused = False

    bal_before = []
    bal_after = []
    dd_series = []
    risk_used = []

    for r in df["R"].values:
        bal_before.append(bal)
        dd = bal / running_max - 1.0

        base_risk = get_risk(dd, schedule)

        # Pause-Hysteresis
        if base_risk == 0.0:
            paused = True
        elif paused and dd > -hysteresis_recover:
            paused = False

        risk = 0.0 if paused else base_risk

        if risk > 0.0:
            bal = bal * (1.0 + risk * float(r))

        running_max = max(running_max, bal)
        bal_after.append(bal)
        dd_series.append(dd)
        risk_used.append(risk)

    return pd.DataFrame({
        "t":          df["t"].values,
        "symbol":     df["symbol"].values if "symbol" in df.columns else None,
        "R":          df["R"].values,
        "bal_before": bal_before,
        "balance":    bal_after,
        "dd_pct":     dd_series,
        "risk_used":  risk_used,
    })


def compute_metrics(eq: pd.DataFrame, start: float) -> dict:
    final = eq["balance"].iloc[-1]
    ret_pct = (final / start - 1.0) * 100

    running_max = eq["balance"].cummax()
    dd_live = (eq["balance"] / running_max - 1.0) * 100
    max_dd_pct = dd_live.min()

    active = eq[eq["risk_used"] > 0]
    wins = active[active["R"] > 0]["R"].sum()
    losses_abs = -active[active["R"] <= 0]["R"].sum()
    pf = wins / losses_abs if losses_abs > 0 else float("inf")

    n_taken = len(active)
    n_skipped = len(eq) - n_taken
    n_wins = int((active["R"] > 0).sum())
    n_losses = int((active["R"] <= 0).sum())
    wr = (n_wins / n_taken * 100) if n_taken > 0 else 0.0

    return {
        "final": final,
        "return_pct": ret_pct,
        "max_dd_pct": max_dd_pct,
        "pf": pf,
        "wr": wr,
        "n_taken": n_taken,
        "n_skipped": n_skipped,
        "n_wins": n_wins,
        "n_losses": n_losses,
    }


def monthly_returns(eq: pd.DataFrame) -> pd.DataFrame:
    """Liefert echte Monats-Returns (Balance am Monatsanfang bis Monatsende)."""
    df = eq.copy()
    df["t"] = pd.to_datetime(df["t"], utc=True)
    df["month"] = df["t"].dt.strftime("%Y-%m")

    g = df.groupby("month")
    out = pd.DataFrame({
        "start_bal":     g["bal_before"].first(),
        "end_bal":       g["balance"].last(),
        "trades_taken":  g["risk_used"].apply(lambda x: int((x > 0).sum())),
        "trades_skipped": g["risk_used"].apply(lambda x: int((x <= 0).sum())),
        "sum_r_signal":  g["R"].sum().round(2),
    })
    out["return_pct"] = ((out["end_bal"] / out["start_bal"] - 1.0) * 100).round(2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--start", type=float, default=DEFAULT_START)
    ap.add_argument("--plot", action="store_true",
                    help="Speichere Equity-Overlay-Plot")
    ap.add_argument("--plot-out", default="results/dynamic_risk_overlay.png")
    ap.add_argument("--monthly-out", default="results/dynamic_risk_monthly.csv")
    ap.add_argument("--hysteresis", type=float, default=0.02,
                    help="Recovery-Schwelle fuer Pause-Mode (default 0.02 = -2%%)")
    args = ap.parse_args()

    # Input laden
    df = pd.read_csv(args.input)
    t_col = "close_time" if "close_time" in df.columns else "entry_time"
    r_col = "R" if "R" in df.columns else "total_r"
    df = df.rename(columns={t_col: "t", r_col: "R"})
    df["t"] = pd.to_datetime(df["t"], utc=True, errors="coerce")
    df = df.dropna(subset=["t"]).sort_values("t").reset_index(drop=True)

    print("=" * 86)
    print(f"DYNAMIC RISK SIMULATION")
    print(f"Input:             {args.input}   ({len(df)} Trades)")
    print(f"Start-Balance:     ${args.start:,.2f}")
    print(f"Pause-Hysteresis:  Pause bleibt bis DD besser als -{args.hysteresis*100:.1f}%")
    print("=" * 86)

    results = []
    equity_curves = {}
    monthly_by_variant = {}

    for name, schedule in SCHEDULES.items():
        eq = simulate(df, args.start, schedule, hysteresis_recover=args.hysteresis)
        m = compute_metrics(eq, args.start)
        results.append({"name": name, **m})
        equity_curves[name] = eq
        monthly_by_variant[name] = monthly_returns(eq)

    # Vergleichs-Tabelle
    print(f"\n{'Variante':<28} {'Return':>10} {'MaxDD':>9} {'PF':>6} {'WR':>7} "
          f"{'Taken':>7} {'Skipped':>8} {'FTMO-safe':>11}")
    print("-" * 86)
    for r in results:
        pf_str = f"{r['pf']:.2f}" if r["pf"] != float("inf") else "inf"
        ftmo = "JA" if r["max_dd_pct"] > -10.0 else "NEIN"
        print(f"{r['name']:<28} {r['return_pct']:>9.2f}% {r['max_dd_pct']:>8.2f}% "
              f"{pf_str:>6} {r['wr']:>6.1f}% {r['n_taken']:>7} {r['n_skipped']:>8} "
              f"{ftmo:>11}")

    # Empfehlung
    baseline = next(r for r in results if r["name"] == "baseline_0.7pct_flat")
    print("\n" + "=" * 86)
    print("DELTA VS BASELINE")
    print("=" * 86)
    for r in results:
        if r["name"] == baseline["name"]:
            continue
        dd_red = baseline["max_dd_pct"] - r["max_dd_pct"]
        ret_cost = baseline["return_pct"] - r["return_pct"]
        print(f"  {r['name']:<28}  DD-Reduktion: {dd_red:+6.2f}pp   "
              f"Return-Kosten: {ret_cost:+6.2f}pp")

    # Killer-Months Vergleich: baseline vs bestes FTMO-safe Stufen-A-Schema
    target_variant = "stufe_F_graduated_no_pause"
    if target_variant in monthly_by_variant:
        print("\n" + "=" * 86)
        print(f"KILLER-MONATE: baseline vs {target_variant}")
        print("=" * 86)
        killer_months = ["2024-06", "2024-04", "2023-04", "2023-08",
                         "2021-10", "2021-08", "2026-01", "2025-10", "2022-11"]
        b = monthly_by_variant["baseline_0.7pct_flat"]
        a = monthly_by_variant[target_variant]
        print(f"{'Monat':<10} {'Baseline Ret':>14} {target_variant + ' Ret':>28} "
              f"{'Skipped':>9}")
        for m in killer_months:
            if m not in b.index or m not in a.index:
                continue
            br = b.loc[m, "return_pct"]
            ar = a.loc[m, "return_pct"]
            sk = a.loc[m, "trades_skipped"]
            print(f"{m:<10} {br:>13.2f}% {ar:>27.2f}% {sk:>9}")

    # Monthly CSV export (baseline + Stufe A)
    os.makedirs(os.path.dirname(args.monthly_out) or ".", exist_ok=True)
    for name, m in monthly_by_variant.items():
        path = args.monthly_out.replace(".csv", f"_{name}.csv")
        m.to_csv(path)
    print(f"\nMonats-CSVs: {args.monthly_out.replace('.csv', '_*.csv')}")

    # Plot
    if args.plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(15, 10),
                                            gridspec_kw={"height_ratios": [2, 1]})
            colors = plt.cm.tab10.colors
            for i, (name, eq) in enumerate(equity_curves.items()):
                ax1.plot(pd.to_datetime(eq["t"]), eq["balance"],
                         label=name, linewidth=1.2, color=colors[i % len(colors)])
            ax1.set_title("Dynamic Risk — Equity-Kurven Overlay")
            ax1.set_ylabel("Balance ($)")
            ax1.legend(loc="upper left", fontsize=8)
            ax1.grid(True, alpha=0.3)
            ax1.axhline(args.start, color="black", linestyle=":", alpha=0.5)

            for i, (name, eq) in enumerate(equity_curves.items()):
                rm = eq["balance"].cummax()
                dd = (eq["balance"] / rm - 1.0) * 100
                ax2.plot(pd.to_datetime(eq["t"]), dd,
                         label=name, linewidth=1.0, color=colors[i % len(colors)])
            ax2.axhline(-10, color="red", linestyle="--", alpha=0.7, label="FTMO -10%")
            ax2.axhline(-5,  color="orange", linestyle="--", alpha=0.5, label="FTMO Daily -5%")
            ax2.set_title("Drawdown-Verlauf")
            ax2.set_ylabel("DD (%)")
            ax2.legend(loc="lower left", fontsize=8)
            ax2.grid(True, alpha=0.3)

            fig.tight_layout()
            os.makedirs(os.path.dirname(args.plot_out) or ".", exist_ok=True)
            fig.savefig(args.plot_out, dpi=120)
            print(f"Plot: {args.plot_out}")
        except ImportError:
            print("matplotlib nicht installiert — Plot uebersprungen.")

    return 0


if __name__ == "__main__":
    sys.exit(main())