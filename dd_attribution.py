#!/usr/bin/env python3
"""
dd_attribution.py
-----------------
Drawdown-Attribution fuer das 3-Symbol-Portfolio.

Liest results/trades_portfolio.csv (aus aggregate_multi.py), nutzt die
bereits berechnete 'equity_after'-Spalte als Balance-Kurve und
identifiziert die groessten DD-Phasen. Fuer jede Phase (und global)
wird aufgeschluesselt, welche Dimension den DD getrieben hat:

    - Symbol           (EURUSD / XAUUSD / USDJPY)
    - Direction/Side   (long / short)
    - Zone-Kind        (OB / FVG / ...) - falls per-symbol CSVs vorhanden
    - Session          (Asia / London / NY)
    - Month            (YYYY-MM)

Unterstuetzt 2 CSV-Layouts:
  (1) Portfolio-Aggregat (results/trades_portfolio.csv):
        close_time, symbol, side, R, risk_amount, pnl, equity_after, day_start
  (2) Per-Symbol-Trades (results/trades_EURUSD.csv etc.):
        entry_time, direction, zone_kind, entry, sl, tp1, tp2, exits, total_r, bars_held

Usage:
    python3 dd_attribution.py
    python3 dd_attribution.py --enrich-zones
    python3 dd_attribution.py --top 10 --export results/_dd/equity.csv
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from typing import List

import pandas as pd


# ----------------------------------------------------------------------
# CONFIG DEFAULTS
# ----------------------------------------------------------------------

DEFAULT_INPUT = "results/trades_portfolio.csv"
DEFAULT_TOP_N = 5
DEFAULT_RISK_PER_TRADE = 0.007
DEFAULT_START_BALANCE = 10_000.0


# ----------------------------------------------------------------------
# COLUMN DETECTION
# ----------------------------------------------------------------------

TIME_COL_CANDIDATES   = ["close_time", "exit_time", "entry_time", "time", "timestamp", "entry_dt"]
R_COL_CANDIDATES      = ["R", "total_r", "r", "r_multiple"]
SYM_COL_CANDIDATES    = ["symbol", "sym", "pair"]
DIR_COL_CANDIDATES    = ["side", "direction", "dir"]
ZONE_COL_CANDIDATES   = ["zone_kind", "zone", "setup"]
EQUITY_COL_CANDIDATES = ["equity_after", "equity", "balance"]


def pick_col(df: pd.DataFrame, candidates: List[str], required: bool = True, what: str = "") -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    if required:
        raise KeyError(
            f"Keine {what}-Spalte gefunden. "
            f"Kandidaten: {candidates}. Vorhanden: {list(df.columns)}"
        )
    return None


# ----------------------------------------------------------------------
# SESSION MAPPING (UTC-Stunden)
# ----------------------------------------------------------------------

def session_of(dt: pd.Timestamp) -> str:
    h = dt.hour
    if 0 <= h < 7:
        return "Asia"
    if 7 <= h < 13:
        return "London"
    if 13 <= h < 22:
        return "NY"
    return "Asia"


# ----------------------------------------------------------------------
# DATA LOADING
# ----------------------------------------------------------------------

def load_trades(path: str, enrich_zones: bool = False) -> pd.DataFrame:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Trade-CSV nicht gefunden: {path}")

    df = pd.read_csv(path)
    print(f"  CSV-Spalten: {list(df.columns)}")

    t_col = pick_col(df, TIME_COL_CANDIDATES, what="Zeit")
    r_col = pick_col(df, R_COL_CANDIDATES,    what="R")
    s_col = pick_col(df, SYM_COL_CANDIDATES, required=False)
    d_col = pick_col(df, DIR_COL_CANDIDATES, required=False)
    z_col = pick_col(df, ZONE_COL_CANDIDATES, required=False)
    e_col = pick_col(df, EQUITY_COL_CANDIDATES, required=False)

    rename_map = {r_col: "r", t_col: "t"}
    if s_col: rename_map[s_col] = "symbol"
    if d_col: rename_map[d_col] = "direction"
    if z_col: rename_map[z_col] = "zone_kind"
    if e_col: rename_map[e_col] = "equity_after"
    df = df.rename(columns=rename_map)

    df["t"] = pd.to_datetime(df["t"], utc=True, errors="coerce")
    if df["t"].isna().any():
        bad = df["t"].isna().sum()
        print(f"  WARN: {bad} Trades mit unparsebarem Timestamp — werden verworfen.")
        df = df[df["t"].notna()].copy()

    df = df.sort_values("t").reset_index(drop=True)

    df["month"]   = df["t"].dt.strftime("%Y-%m")
    df["session"] = df["t"].apply(session_of)

    for col in ("symbol", "direction", "zone_kind"):
        if col not in df.columns:
            df[col] = "UNKNOWN"

    df["direction"] = df["direction"].astype(str).str.lower().replace({
        "buy": "long", "sell": "short", "l": "long", "s": "short",
    })

    if enrich_zones and (df["zone_kind"] == "UNKNOWN").all():
        df = enrich_zone_kind(df, base_dir=os.path.dirname(path) or "results")

    return df


def enrich_zone_kind(df: pd.DataFrame, base_dir: str = "results") -> pd.DataFrame:
    print(f"  [ENRICH] Versuche zone_kind aus per-symbol CSVs zu holen ...")
    out = df.copy()
    out["zone_kind"] = "UNKNOWN"

    for sym in out["symbol"].unique():
        path = os.path.join(base_dir, f"trades_{sym}.csv")
        if not os.path.isfile(path):
            print(f"  [ENRICH] {sym}: {path} nicht gefunden, skip.")
            continue
        sub = pd.read_csv(path)
        if "zone_kind" not in sub.columns or "entry_time" not in sub.columns:
            print(f"  [ENRICH] {sym}: fehlende Spalten, skip.")
            continue
        sub["entry_time"] = pd.to_datetime(sub["entry_time"], utc=True, errors="coerce")
        sub = sub.dropna(subset=["entry_time"]).sort_values("entry_time")

        mask_sym = out["symbol"] == sym
        rows = out[mask_sym].copy().sort_values("t")
        merged = pd.merge_asof(
            rows,
            sub[["entry_time", "zone_kind"]].rename(columns={"zone_kind": "zk_join"}),
            left_on="t",
            right_on="entry_time",
            direction="nearest",
            tolerance=pd.Timedelta("2D"),
        )
        orig_idx = out[mask_sym].index
        out.loc[orig_idx, "zone_kind"] = merged["zk_join"].fillna("UNKNOWN").values

    missing = (out["zone_kind"] == "UNKNOWN").sum()
    if missing > 0:
        print(f"  [ENRICH] {missing} Trades ohne zone_kind-Match (werden als UNKNOWN gefuehrt).")
    return out


# ----------------------------------------------------------------------
# EQUITY + DD
# ----------------------------------------------------------------------

def compute_equity_and_dd(df: pd.DataFrame, start_balance: float, risk_per_trade: float) -> pd.DataFrame:
    out = df.copy()

    if "equity_after" in out.columns and out["equity_after"].notna().all():
        print(f"  [EQUITY] Nutze vorhandene 'equity_after'-Spalte (Aggregator-Kurve).")
        out["balance"] = out["equity_after"].astype(float)
    else:
        print(f"  [EQUITY] Berechne compound-Kurve aus R: start=${start_balance:,.2f}, risk={risk_per_trade*100:.2f}%")
        bal = start_balance
        balances = []
        for r in out["r"].values:
            bal = bal * (1.0 + risk_per_trade * float(r))
            balances.append(bal)
        out["balance"] = balances

    out["running_max"] = out["balance"].cummax()
    out["dd_dollar"] = out["balance"] - out["running_max"]
    out["dd_pct"] = (out["balance"] / out["running_max"]) - 1.0
    return out


@dataclass
class DDPeriod:
    idx_peak:    int
    idx_trough:  int
    idx_recover: int | None
    peak_balance: float
    trough_balance: float
    dd_pct:    float
    dd_dollar: float
    t_peak:    pd.Timestamp
    t_trough:  pd.Timestamp
    t_recover: pd.Timestamp | None
    trades: pd.DataFrame = field(default_factory=pd.DataFrame)


def find_dd_periods(eq: pd.DataFrame, top_n: int) -> List[DDPeriod]:
    bal = eq["balance"].values
    n = len(bal)
    periods: List[DDPeriod] = []
    i = 0
    while i < n:
        peak_val = bal[i]
        idx_peak = i
        j = i + 1
        trough_val = peak_val
        idx_trough = idx_peak
        while j < n and bal[j] < peak_val:
            if bal[j] < trough_val:
                trough_val = bal[j]
                idx_trough = j
            j += 1
        if j > i + 1:
            idx_recover = j if j < n else None
            dd_pct = (trough_val / peak_val) - 1.0
            dd_dollar = trough_val - peak_val
            periods.append(DDPeriod(
                idx_peak=idx_peak,
                idx_trough=idx_trough,
                idx_recover=idx_recover,
                peak_balance=peak_val,
                trough_balance=trough_val,
                dd_pct=dd_pct,
                dd_dollar=dd_dollar,
                t_peak=eq["t"].iloc[idx_peak],
                t_trough=eq["t"].iloc[idx_trough],
                t_recover=eq["t"].iloc[idx_recover] if idx_recover is not None else None,
            ))
        i = j if j > i else i + 1

    for p in periods:
        slice_df = eq.iloc[p.idx_peak + 1 : p.idx_trough + 1].copy()
        p.trades = slice_df

    periods.sort(key=lambda p: p.dd_pct)
    return periods[:top_n]


# ----------------------------------------------------------------------
# ATTRIBUTION
# ----------------------------------------------------------------------

def breakdown(trades: pd.DataFrame, by: str) -> pd.DataFrame:
    if trades.empty or by not in trades.columns:
        return pd.DataFrame()

    g = trades.groupby(by)
    out = pd.DataFrame({
        "trades": g.size(),
        "R_sum":  g["r"].sum().round(2),
        "R_mean": g["r"].mean().round(3),
        "wins":   g.apply(lambda x: int((x["r"] > 0).sum())),
        "losses": g.apply(lambda x: int((x["r"] <= 0).sum())),
    })
    out["win_rate"] = (out["wins"] / out["trades"] * 100).round(1)

    total_neg_r = trades[trades["r"] <= 0]["r"].sum()
    if total_neg_r != 0:
        out["pct_of_losses"] = (
            g.apply(lambda x: x[x["r"] <= 0]["r"].sum() / total_neg_r * 100).round(1)
        )
    else:
        out["pct_of_losses"] = 0.0
    return out.sort_values("R_sum")


def print_breakdown(title: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    print(f"\n  >> {title}")
    print(df.to_string())


def print_header(text: str, char: str = "=") -> None:
    print("\n" + char * 78)
    print(f"  {text}")
    print(char * 78)


def report_global(eq: pd.DataFrame) -> None:
    final_bal  = eq["balance"].iloc[-1]
    max_dd_pct = eq["dd_pct"].min()
    max_dd_dol = eq["dd_dollar"].min()
    n_trades   = len(eq)
    total_r    = eq["r"].sum()
    winners    = (eq["r"] > 0).sum()
    losers     = (eq["r"] <= 0).sum()

    first_r = eq["r"].iloc[0]
    if abs(first_r) > 1e-9:
        start_est = eq["balance"].iloc[0] / (1.0 + DEFAULT_RISK_PER_TRADE * first_r)
    else:
        start_est = eq["balance"].iloc[0]

    print_header("PORTFOLIO GLOBAL METRICS")
    print(f"  Start-Balance (est.): ${start_est:>12,.2f}")
    print(f"  End-Balance:          ${final_bal:>12,.2f}")
    print(f"  Return:                {(final_bal / start_est - 1)*100:>11.2f}%")
    print(f"  MaxDD (pct):           {max_dd_pct*100:>11.2f}%")
    print(f"  MaxDD (dollar):       ${max_dd_dol:>12,.2f}")
    print(f"  Trades:                {n_trades:>11d}")
    print(f"  Total R:               {total_r:>11.2f}")
    print(f"  Win-Rate:              {winners/n_trades*100:>10.1f}%   ({winners}W / {losers}L)")


def report_period(p: DDPeriod, rank: int) -> None:
    print_header(f"DD-PHASE #{rank}   {p.dd_pct*100:.2f}%  (${p.dd_dollar:,.2f})", char="-")
    dur_days = (p.t_trough - p.t_peak).days
    rec_str = (
        f"{p.t_recover.strftime('%Y-%m-%d %H:%M')} "
        f"(Recovery nach {(p.t_recover - p.t_trough).days}d)"
        if p.t_recover is not None else "NIE RECOVERED (offen bis Ende)"
    )
    print(f"  Peak:     {p.t_peak.strftime('%Y-%m-%d %H:%M')}   ${p.peak_balance:,.2f}")
    print(f"  Trough:   {p.t_trough.strftime('%Y-%m-%d %H:%M')}   ${p.trough_balance:,.2f}   "
          f"(nach {dur_days}d)")
    print(f"  Recover:  {rec_str}")
    print(f"  Trades in Phase: {len(p.trades)}")

    for dim in ("symbol", "direction", "zone_kind", "session", "month"):
        print_breakdown(f"Breakdown nach {dim.upper()}:", breakdown(p.trades, dim))


def report_global_attribution(eq: pd.DataFrame) -> None:
    print_header("GLOBAL DD-ATTRIBUTION  (nur Verlust-Trades)", char="=")
    losers_only = eq[eq["r"] <= 0].copy()
    total_loss_r = losers_only["r"].sum()
    print(f"  Anzahl Verlust-Trades: {len(losers_only)}")
    print(f"  Summe Verlust-R:       {total_loss_r:.2f}")

    for dim in ("symbol", "direction", "zone_kind", "session", "month"):
        print_breakdown(f"Losses nach {dim.upper()}:", breakdown(losers_only, dim))


def main() -> int:
    ap = argparse.ArgumentParser(description="Drawdown-Attribution fuer Portfolio-Trades.")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--top", type=int, default=DEFAULT_TOP_N)
    ap.add_argument("--risk", type=float, default=DEFAULT_RISK_PER_TRADE)
    ap.add_argument("--start-balance", type=float, default=DEFAULT_START_BALANCE)
    ap.add_argument("--export", default=None)
    ap.add_argument("--enrich-zones", action="store_true",
                    help="Zone-Kind aus per-symbol CSVs nachladen")
    args = ap.parse_args()

    print_header("DD-ATTRIBUTION")
    print(f"  Input:          {args.input}")
    print(f"  Risk/Trade:     {args.risk*100:.2f}%")
    print(f"  Start-Balance:  ${args.start_balance:,.2f}")
    print(f"  Top-DD-Phasen:  {args.top}")
    print(f"  Enrich-Zones:   {args.enrich_zones}")

    df = load_trades(args.input, enrich_zones=args.enrich_zones)
    if df.empty:
        print("ERROR: keine Trades im CSV.")
        return 1

    eq = compute_equity_and_dd(df, args.start_balance, args.risk)

    report_global(eq)
    report_global_attribution(eq)

    periods = find_dd_periods(eq, args.top)
    print_header(f"TOP-{len(periods)} DRAWDOWN-PHASEN")
    for i, p in enumerate(periods, start=1):
        report_period(p, i)

    if args.export:
        os.makedirs(os.path.dirname(args.export) or ".", exist_ok=True)
        eq.to_csv(args.export, index=False)
        print(f"\n  Equity-Kurve exportiert nach: {args.export}")

    print_header("FERTIG.")
    print("  Naechste Schritte je nach Ergebnis:")
    print("    - Ein Symbol/Direction konzentriert?  -> Filter-Refinement (Phase 5)")
    print("    - Verluste gleichmaessig verteilt?    -> Dynamic Risk (Phase 2)")
    print("    - Ein Monat/Session schiessende DD?   -> Zeit-/Regime-Filter (Phase 5)")
    return 0


if __name__ == "__main__":
    sys.exit(main())