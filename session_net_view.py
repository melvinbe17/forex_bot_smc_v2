"""
session_net_view.py  (forex_bot_smc)
====================================
Per-Symbol Session-Analyse.

Session-Filter aktuell (config.py SESSION_KILLZONES_UTC):
  (7-10 UTC London Open) + (12-15 UTC NY Open / LDN-NY Overlap)

Dieses Script zeigt pro Symbol + pro Entry-Stunde:
  - Anzahl Trades (n)
  - Wins / Losses / Win-Rate
  - Net-R (Summe aller R-Multiples, Gewinne UND Verluste)
  - Avg-R, Profit-Factor
  - Best-R / Worst-R

Ziel: datenbasiert "Dead Hours" rausfinden (Net-R < 0) und als
Kandidaten fuer zusaetzlichen Hour-Blacklist-Filter markieren.

Usage:
  python3 session_net_view.py

Erwartet: results/trades_{SYMBOL}.csv mit Spalte 'total_r' und 'entry_time'.
"""
from __future__ import annotations
from pathlib import Path
import pandas as pd

SYMBOLS = ["EURUSD", "XAUUSD", "USDJPY"]
TRADES_DIR = Path("results")


def load_trades(symbol: str):
    csv_path = TRADES_DIR / f"trades_{symbol}.csv"
    if not csv_path.exists():
        print(f"[WARN] {csv_path} nicht gefunden, skip {symbol}")
        return None
    df = pd.read_csv(csv_path)
    df["entry_time"] = pd.to_datetime(df["entry_time"])
    df["hour_utc"] = df["entry_time"].dt.hour
    df["weekday"] = df["entry_time"].dt.weekday  # 0=Mo, 6=So
    return df


def compute_metrics(g: pd.DataFrame) -> dict:
    n = len(g)
    wins_mask = g["total_r"] > 0
    wins = int(wins_mask.sum())
    losses = n - wins
    wr = 100.0 * wins / n if n > 0 else 0.0
    sum_r = float(g["total_r"].sum())
    avg_r = float(g["total_r"].mean()) if n > 0 else 0.0
    best_r = float(g["total_r"].max()) if n > 0 else 0.0
    worst_r = float(g["total_r"].min()) if n > 0 else 0.0
    gross_win = float(g[wins_mask]["total_r"].sum())
    gross_loss = abs(float(g[~wins_mask]["total_r"].sum()))
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    return {"n": n, "W": wins, "L": losses, "WR%": wr,
            "sumR": sum_r, "avgR": avg_r, "PF": pf,
            "best": best_r, "worst": worst_r}


def breakdown(df: pd.DataFrame, key: str) -> pd.DataFrame:
    rows = []
    for k, g in df.groupby(key):
        m = compute_metrics(g)
        m[key] = k
        rows.append(m)
    return pd.DataFrame(rows).sort_values(key).reset_index(drop=True)


def fmt_pf(pf: float) -> str:
    return f"{pf:.2f}" if pf != float("inf") else "inf"


def print_hour_table(symbol: str, df: pd.DataFrame, hours: pd.DataFrame):
    print(f"\n--- {symbol}   (by Entry-Hour UTC) ---")
    print(f"{'hr':>3} {'n':>4} {'W':>4} {'L':>4} "
          f"{'WR%':>6} {'sumR':>8} {'avgR':>7} {'PF':>6} "
          f"{'best':>6} {'worst':>7}  flag")
    print("-" * 75)
    for _, r in hours.iterrows():
        flag = ""
        if r["sumR"] < 0:
            flag = "DEAD"
        elif r["PF"] < 1.0:
            flag = "weak"
        print(f"{int(r['hour_utc']):>3d} {int(r['n']):>4d} "
              f"{int(r['W']):>4d} {int(r['L']):>4d} "
              f"{r['WR%']:>6.2f} {r['sumR']:>8.2f} "
              f"{r['avgR']:>7.3f} {fmt_pf(r['PF']):>6} "
              f"{r['best']:>6.2f} {r['worst']:>7.2f}  {flag}")
    tot = compute_metrics(df)
    print("-" * 75)
    print(f"{'TOT':>3} {tot['n']:>4d} {tot['W']:>4d} {tot['L']:>4d} "
          f"{tot['WR%']:>6.2f} {tot['sumR']:>8.2f} {tot['avgR']:>7.3f} "
          f"{fmt_pf(tot['PF']):>6} {tot['best']:>6.2f} {tot['worst']:>7.2f}")


def print_weekday_table(symbol: str, weekdays: pd.DataFrame):
    wd_names = {0: "Mo", 1: "Di", 2: "Mi", 3: "Do", 4: "Fr", 5: "Sa", 6: "So"}
    print(f"\n--- {symbol}   (by Weekday) ---")
    print(f"{'day':>4} {'n':>4} {'W':>4} {'L':>4} "
          f"{'WR%':>6} {'sumR':>8} {'avgR':>7} {'PF':>6}")
    print("-" * 55)
    for _, r in weekdays.iterrows():
        flag = " <- DEAD" if r["sumR"] < 0 else ""
        name = wd_names.get(int(r["weekday"]), "?")
        print(f"{name:>4} {int(r['n']):>4d} {int(r['W']):>4d} "
              f"{int(r['L']):>4d} {r['WR%']:>6.2f} {r['sumR']:>8.2f} "
              f"{r['avgR']:>7.3f} {fmt_pf(r['PF']):>6}{flag}")


def suggest_blacklist(symbol: str, hours: pd.DataFrame) -> list:
    dead = hours[hours["sumR"] < 0]
    if len(dead) == 0:
        print(f"\n  {symbol}: keine Dead-Hours")
        return []
    total_r = float(dead["sumR"].sum())
    total_n = int(dead["n"].sum())
    hrs = sorted(dead["hour_utc"].astype(int).tolist())
    print(f"\n  {symbol} Dead-Hour-Kandidaten: {hrs}")
    print(f"    Trades in Dead-Hours: {total_n}")
    print(f"    Net-R in Dead-Hours : {total_r:+.2f}")
    print(f"    -> ohne diese Stunden: -{total_n} Trades, +{-total_r:.2f}R")
    return hrs


def main():
    print("=" * 78)
    print("SESSION-NET-VIEW  |  Per-Symbol Per-Hour + Per-Weekday Breakdown")
    print("Session-Filter aktiv: 7-10 UTC (LDN) + 12-15 UTC (NY Overlap)")
    print("=" * 78)

    all_dead = {}
    for sym in SYMBOLS:
        df = load_trades(sym)
        if df is None:
            continue
        hours = breakdown(df, "hour_utc")
        weekdays = breakdown(df, "weekday")
        print_hour_table(sym, df, hours)
        print_weekday_table(sym, weekdays)
        hrs = suggest_blacklist(sym, hours)
        if hrs:
            all_dead[sym] = hrs

    print("\n" + "=" * 78)
    print("DEAD-HOUR SUMMARY (Kandidaten fuer Hour-Blacklist)")
    print("=" * 78)
    if not all_dead:
        print("  Keine Dead-Hours -> Session-Filter ist bereits clean.")
    else:
        for sym, hrs in all_dead.items():
            print(f"  {sym}: exclude hours {hrs}")
    print("=" * 78)


if __name__ == "__main__":
    main()
