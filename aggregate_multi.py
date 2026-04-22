"""
aggregate_multi.py
------------------
Aggregiert per-Symbol Backtest-Trades auf einen gemeinsamen FTMO-Account.

Shared $10k-Account, 0.7% Risk pro Trade (compound), FTMO-Regeln:
  - Daily Max Loss  -5%  (vom Start-of-Day Equity)
  - Total Max Loss -10%  (vom Start-Equity, statisch)

Workflow:
    # 1) pro Symbol Backtest mit explizitem Trade-Output:
    python3 backtest_m15.py --symbol EURUSD --limit 0 \\
        --trades-out results/trades_EURUSD.csv
    python3 backtest_m15.py --symbol XAUUSD --limit 0 \\
        --trades-out results/trades_XAUUSD.csv
    python3 backtest_m15.py --symbol GBPUSD --limit 0 \\
        --trades-out results/trades_GBPUSD.csv

    # 2) Portfolio aggregieren:
    python3 aggregate_multi.py EURUSD XAUUSD GBPUSD
    # oder nur 2 Symbole:
    python3 aggregate_multi.py EURUSD XAUUSD
"""

from __future__ import annotations

import os
import sys
from typing import List

import numpy as np
import pandas as pd

try:
    import matplotlib.pyplot as plt
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


# --- Konfiguration ---------------------------------------------------------

INITIAL_EQUITY      = 10_000.0
RISK_PER_TRADE      = 0.007    # 0.7% pro Trade (muss zu config.py passen)
FTMO_DAILY_MAX_LOSS = 0.05     # -5%
FTMO_TOTAL_MAX_LOSS = 0.10     # -10%

RESULTS_DIR = "results"


# --- Helpers ---------------------------------------------------------------

def pick_col(df: pd.DataFrame, candidates: List[str], label: str) -> str:
    for c in candidates:
        if c in df.columns:
            return c
    raise RuntimeError(
        f"Konnte Spalte '{label}' nicht finden.\n"
        f"  Verfuegbar: {list(df.columns)}\n"
        f"  Gesucht:    {candidates}"
    )


def load_trades(symbol: str, path: str) -> pd.DataFrame | None:
    if not os.path.exists(path):
        print(f"  [SKIP] {path} existiert nicht.")
        return None
    df = pd.read_csv(path)
    if len(df) == 0:
        print(f"  [SKIP] {path} ist leer.")
        return None

    time_col = pick_col(df,
        ["close_time", "exit_time", "time_exit", "exit", "close", "time",
         "entry_time", "open_time"],
        "close_time")
    r_col = pick_col(df,
        ["total_r", "R", "r", "r_multiple", "pnl_r", "result_r", "R_multiple"],
        "R")
    side_col = None
    for c in ["side", "direction", "dir", "type"]:
        if c in df.columns:
            side_col = c
            break

    out = pd.DataFrame({
        "close_time": pd.to_datetime(df[time_col]),
        "R":          df[r_col].astype(float),
        "side":       df[side_col] if side_col else "-",
        "symbol":     symbol,
    })
    return out.sort_values("close_time").reset_index(drop=True)


# --- Simulation ------------------------------------------------------------

def simulate_portfolio(trades: pd.DataFrame) -> dict:
    equity       = INITIAL_EQUITY
    start_equity = INITIAL_EQUITY
    daily_start: dict = {}

    rows = []
    equity_points = []
    daily_violations = []
    total_violated = False

    running_max = INITIAL_EQUITY
    max_dd_pct  = 0.0

    for _, t in trades.iterrows():
        day = t["close_time"].date()
        if day not in daily_start:
            daily_start[day] = equity
        sod = daily_start[day]

        risk_amount = equity * RISK_PER_TRADE
        pnl         = risk_amount * t["R"]
        equity     += pnl

        # Drawdowns
        running_max = max(running_max, equity)
        dd = (equity - running_max) / running_max * 100
        if dd < max_dd_pct:
            max_dd_pct = dd

        # FTMO Daily
        dod = (equity - sod) / sod
        if dod < -FTMO_DAILY_MAX_LOSS:
            daily_violations.append((day, dod * 100))

        # FTMO Total (statisch vom Start)
        if (equity - start_equity) / start_equity < -FTMO_TOTAL_MAX_LOSS:
            total_violated = True

        rows.append({
            "close_time":   t["close_time"],
            "symbol":       t["symbol"],
            "side":         t["side"],
            "R":            t["R"],
            "risk_amount":  risk_amount,
            "pnl":          pnl,
            "equity_after": equity,
            "day_start":    sod,
        })
        equity_points.append((t["close_time"], equity))

    combined = pd.DataFrame(rows)
    eq_df    = pd.DataFrame(equity_points, columns=["time", "equity"])

    return {
        "combined":             combined,
        "equity":               eq_df,
        "final_equity":         equity,
        "total_return_pct":     (equity / start_equity - 1) * 100,
        "max_dd_pct":           max_dd_pct,
        "daily_violations":     daily_violations,
        "total_violated":       total_violated,
        "n_trading_days":       len(daily_start),
    }


# --- Reporting -------------------------------------------------------------

def r_summary(trades: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for sym, g in list(trades.groupby("symbol")) + [("TOTAL", trades)]:
        wins   = (g["R"] > 0).sum()
        losses = (g["R"] <= 0).sum()
        gp     = g.loc[g["R"] > 0, "R"].sum()
        gl     = -g.loc[g["R"] <= 0, "R"].sum()
        pf     = gp / gl if gl > 0 else float("inf")
        rows.append({
            "symbol": sym,
            "n":      len(g),
            "W":      int(wins),
            "L":      int(losses),
            "WR%":    100 * wins / len(g) if len(g) else 0,
            "sumR":   g["R"].sum(),
            "PF":     pf,
        })
    return pd.DataFrame(rows)


def year_summary(combined: pd.DataFrame) -> pd.DataFrame:
    df = combined.copy()
    df["year"] = df["close_time"].dt.year
    rows = []
    for y, g in df.groupby("year"):
        start_eq = g["equity_after"].iloc[0] - g["pnl"].iloc[0]
        end_eq   = g["equity_after"].iloc[-1]
        rows.append({
            "year":      int(y),
            "n":         len(g),
            "sumR":      g["R"].sum(),
            "pnl_$":     g["pnl"].sum(),
            "start_$":   start_eq,
            "end_$":     end_eq,
            "return%":   (end_eq / start_eq - 1) * 100,
        })
    return pd.DataFrame(rows)


def print_table(df: pd.DataFrame, floats=2):
    fmt = lambda x: f"{x:.{floats}f}" if isinstance(x, float) else str(x)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.{floats}f}"))


# --- Main ------------------------------------------------------------------

def main():
    symbols = sys.argv[1:]
    if not symbols:
        print("Usage:  python3 aggregate_multi.py <SYMBOL1> [<SYMBOL2> ...]")
        print("Bsp.:   python3 aggregate_multi.py EURUSD XAUUSD GBPUSD")
        sys.exit(1)

    os.makedirs(RESULTS_DIR, exist_ok=True)

    print("=" * 72)
    print(f"PORTFOLIO AGGREGATOR  |  Symbols: {', '.join(symbols)}")
    print(f"Start ${INITIAL_EQUITY:,.0f}  |  Risk {RISK_PER_TRADE:.2%}/Trade  "
          f"|  FTMO: Daily -{FTMO_DAILY_MAX_LOSS:.0%}, Total -{FTMO_TOTAL_MAX_LOSS:.0%}")
    print("=" * 72)

    frames = []
    for sym in symbols:
        path = os.path.join(RESULTS_DIR, f"trades_{sym}.csv")
        print(f"[LADE] {sym:<8} <- {path}")
        df = load_trades(sym, path)
        if df is None:
            continue
        print(f"         {len(df):>5} Trades  "
              f"({df['close_time'].iloc[0]} -> {df['close_time'].iloc[-1]})")
        frames.append(df)

    if not frames:
        print("\nKeine Trades gefunden. Erst per-Symbol Backtests mit "
              "--trades-out results/trades_<SYM>.csv laufen lassen.")
        sys.exit(1)

    trades = (pd.concat(frames, ignore_index=True)
                .sort_values("close_time")
                .reset_index(drop=True))
    print(f"\n[OK] Portfolio: {len(trades)} Trades chronologisch gemerged.")

    r = simulate_portfolio(trades)

    print("\n" + "=" * 72)
    print("PER-SYMBOL R-BREAKDOWN")
    print("=" * 72)
    print_table(r_summary(trades))

    print("\n" + "=" * 72)
    print("PORTFOLIO METRIKEN (Shared Account, compound)")
    print("=" * 72)
    print(f"  Final Equity         :  ${r['final_equity']:>12,.2f}")
    print(f"  Total Return         :  {r['total_return_pct']:>12.2f}%")
    print(f"  Max Drawdown         :  {r['max_dd_pct']:>12.2f}%")
    print(f"  Trading Days         :  {r['n_trading_days']:>12}")

    print("\n" + "=" * 72)
    print("FTMO CHECK")
    print("=" * 72)
    dv = r["daily_violations"]
    print(f"  Daily -5%  Violations:  {len(dv)}")
    for d, pct in dv[:10]:
        print(f"     {d}  DayDD={pct:+.2f}%")
    if len(dv) > 10:
        print(f"     ... und {len(dv) - 10} weitere")
    print(f"  Total -10% hit       :  "
          f"{'JA - CHALLENGE FAIL' if r['total_violated'] else 'NEIN - OK'}")

    verdict = (r["total_violated"] is False) and (len(dv) == 0)
    print(f"\n  >>> FTMO-Regeln eingehalten: "
          f"{'JA' if verdict else 'NEIN (siehe oben)'}")

    print("\n" + "=" * 72)
    print("JAHRES-BREAKDOWN (Portfolio)")
    print("=" * 72)
    print_table(year_summary(r["combined"]))

    # Persist
    tr_out = os.path.join(RESULTS_DIR, "trades_portfolio.csv")
    r["combined"].to_csv(tr_out, index=False)
    print(f"\n[OK] Trades:  {tr_out}")

    eq_out = os.path.join(RESULTS_DIR, "equity_portfolio.csv")
    r["equity"].to_csv(eq_out, index=False)
    print(f"[OK] Equity:  {eq_out}")

    if HAS_MPL:
        fig, ax = plt.subplots(figsize=(12, 5))
        ax.plot(r["equity"]["time"], r["equity"]["equity"],
                label="Portfolio Equity", linewidth=1.2)
        ax.axhline(INITIAL_EQUITY, color="gray", linestyle="--",
                   linewidth=0.8, label="Start")
        ax.axhline(INITIAL_EQUITY * (1 - FTMO_TOTAL_MAX_LOSS),
                   color="red", linestyle="--", linewidth=0.8,
                   label="FTMO -10% Limit")
        ax.set_title(f"Portfolio Equity  ({', '.join(symbols)})  "
                     f"@ Risk {RISK_PER_TRADE:.2%}")
        ax.set_ylabel("Equity $")
        ax.legend()
        ax.grid(True, alpha=0.3)
        plot_out = os.path.join(RESULTS_DIR, "equity_portfolio.png")
        fig.tight_layout()
        fig.savefig(plot_out, dpi=100)
        plt.close(fig)
        print(f"[OK] Plot:    {plot_out}")


if __name__ == "__main__":
    main()
