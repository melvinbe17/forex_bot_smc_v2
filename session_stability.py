"""
session_stability.py  (forex_bot_smc)
=====================================
Jahr-fuer-Jahr-Stabilitaets-Check der Dead-Zones, die session_net_view.py
identifiziert hat.

Frage: sind diese Patterns konsistent negativ ueber alle 5 Jahre,
oder nur Artefakte einzelner schlechter Jahre?

Getestete Patterns:
  EURUSD hour=13  |  XAUUSD hour=14  |  USDJPY hour=9  |  USDJPY weekday=Fr

Verdict:
  ROBUST    = negativ in >=4/5 Jahren              -> Filter EMPFOHLEN
  GEMISCHT  = negativ in 3/5 Jahren                -> grenzwertig
  INSTABIL  = negativ in <=2/5 Jahren              -> KEIN Filter (Noise)

Usage:
  python3 session_stability.py
"""
from __future__ import annotations
from pathlib import Path
import pandas as pd

TRADES_DIR = Path("results")

# (symbol, dimension, value, label)
PATTERNS = [
    ("EURUSD", "hour_utc", 13, "EURUSD  hour=13"),
    ("XAUUSD", "hour_utc", 14, "XAUUSD  hour=14"),
    ("USDJPY", "hour_utc",  9, "USDJPY  hour=9  (Noise-Check, -0.30R gesamt)"),
    ("USDJPY", "weekday",   4, "USDJPY  weekday=Fr"),
]

WD_NAMES = {0: "Mo", 1: "Di", 2: "Mi", 3: "Do", 4: "Fr", 5: "Sa", 6: "So"}


def load_trades(symbol: str):
    csv_path = TRADES_DIR / f"trades_{symbol}.csv"
    if not csv_path.exists():
        return None
    df = pd.read_csv(csv_path)
    df["entry_time"] = pd.to_datetime(df["entry_time"])
    df["hour_utc"] = df["entry_time"].dt.hour
    df["weekday"] = df["entry_time"].dt.weekday
    df["year"] = df["entry_time"].dt.year
    return df


def metrics(g: pd.DataFrame) -> dict:
    n = len(g)
    if n == 0:
        return {"n": 0, "wins": 0, "WR%": 0.0, "sumR": 0.0, "avgR": 0.0}
    wins = int((g["total_r"] > 0).sum())
    return {
        "n": n,
        "wins": wins,
        "WR%": 100.0 * wins / n,
        "sumR": float(g["total_r"].sum()),
        "avgR": float(g["total_r"].mean()),
    }


def verdict_for(neg_years: int, total_years: int) -> str:
    if neg_years >= 4:
        return "ROBUST   (Filter EMPFOHLEN)"
    if neg_years == 3:
        return "GEMISCHT (grenzwertig)"
    return "INSTABIL (Noise -> kein Filter)"


def analyze_pattern(symbol: str, dim: str, val, label: str) -> dict:
    df = load_trades(symbol)
    if df is None:
        print(f"[WARN] kein CSV fuer {symbol}")
        return {}
    sub = df[df[dim] == val]
    if len(sub) == 0:
        print(f"[WARN] keine Trades fuer {label}")
        return {}

    print(f"\n--- {label} ---")
    print(f"  Gesamt: {len(sub)} Trades")
    print(f"\n  {'year':>5} {'n':>4} {'W':>3} {'WR%':>6} "
          f"{'sumR':>7} {'avgR':>7}  flag")
    print(f"  " + "-" * 48)

    years = sorted(sub["year"].unique())
    neg_years = 0
    worst = {"year": None, "sumR": 0.0}

    for y in years:
        m = metrics(sub[sub["year"] == y])
        flag = "NEG" if m["sumR"] < 0 else ""
        if m["sumR"] < 0:
            neg_years += 1
            if m["sumR"] < worst["sumR"]:
                worst = {"year": int(y), "sumR": m["sumR"]}
        partial = ""
        if y == 2026:
            partial = " (partial)"
        print(f"  {int(y):>5d}{partial} {m['n']:>4d} {m['wins']:>3d} "
              f"{m['WR%']:>6.2f} {m['sumR']:>7.2f} {m['avgR']:>7.3f}  {flag}")

    all_m = metrics(sub)
    print(f"  " + "-" * 48)
    print(f"  {'TOT':>5}  {all_m['n']:>4d} {all_m['wins']:>3d} "
          f"{all_m['WR%']:>6.2f} {all_m['sumR']:>7.2f} {all_m['avgR']:>7.3f}")

    total_years = len(years)
    verdict = verdict_for(neg_years, total_years)
    print(f"\n  Neg-Years : {neg_years}/{total_years}")
    if worst["year"]:
        print(f"  Worst Year: {worst['year']} ({worst['sumR']:+.2f}R)")
    print(f"  Verdict   : {verdict}")

    return {
        "label": label,
        "neg_years": neg_years,
        "total_years": total_years,
        "worst": worst,
        "verdict": verdict,
        "total_sumR": all_m["sumR"],
    }


def main():
    print("=" * 78)
    print("SESSION STABILITY CHECK  |  Year-by-Year Breakdown der Dead-Zones")
    print("=" * 78)

    results = []
    for sym, dim, val, label in PATTERNS:
        r = analyze_pattern(sym, dim, val, label)
        if r:
            results.append(r)

    # Final Summary
    print("\n" + "=" * 78)
    print("STABILITY SUMMARY")
    print("=" * 78)
    print(f"  {'Pattern':<40} {'NegY':>5} {'TotR':>8}  Verdict")
    print(f"  " + "-" * 74)
    for r in results:
        print(f"  {r['label']:<40} "
              f"{r['neg_years']}/{r['total_years']:<3} "
              f"{r['total_sumR']:>+7.2f}  {r['verdict']}")
    print("=" * 78)


if __name__ == "__main__":
    main()
