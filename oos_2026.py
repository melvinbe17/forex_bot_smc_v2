"""oos_2026.py - Out-of-Sample Analyse auf 2026-Daten"""
import pandas as pd

SYMBOLS = ["EURUSD", "XAUUSD", "USDJPY"]
RCOL = "total_r"
TCOL = "entry_time"

def summarize(df, label):
    n = len(df)
    if n == 0:
        print(f"  {label:22s}  0 Trades")
        return
    wins = (df[RCOL] > 0).sum()
    losses = (df[RCOL] <= 0).sum()
    sumR = df[RCOL].sum()
    wr = wins / n * 100
    gw = df.loc[df[RCOL] > 0, RCOL].sum()
    gl = abs(df.loc[df[RCOL] <= 0, RCOL].sum())
    pf = gw / gl if gl > 0 else float("inf")
    print(f"  {label:22s}  n={n:4d}  W={wins:3d}  L={losses:3d}  "
          f"WR={wr:5.1f}%  sumR={sumR:+7.2f}  PF={pf:.2f}")

print("=" * 80)
print("OUT-OF-SAMPLE 2026 BREAKDOWN")
print("=" * 80)

all_2026 = []
for sym in SYMBOLS:
    path = f"results/trades_{sym}.csv"
    try:
        df = pd.read_csv(path)
    except FileNotFoundError:
        print(f"[SKIP] {path}")
        continue
    df[TCOL] = pd.to_datetime(df[TCOL])
    oos = df[df[TCOL] >= "2026-01-01"].copy()
    oos["_sym"] = sym
    all_2026.append(oos)

    print(f"\n--- {sym} 2026 ---")
    summarize(oos, "GESAMT")
    for dir_ in ["long", "short"]:
        sub = oos[oos["direction"] == dir_]
        if len(sub) > 0:
            summarize(sub, f"  {dir_}")
    for zk in ["OB", "FVG"]:
        sub = oos[oos["zone_kind"] == zk]
        if len(sub) > 0:
            summarize(sub, f"  {zk}")
    if len(oos) > 0:
        oos["_month"] = oos[TCOL].dt.strftime("%Y-%m")
        for m in sorted(oos["_month"].unique()):
            summarize(oos[oos["_month"] == m], f"  {m}")

print("\n" + "=" * 80)
print("PORTFOLIO 2026 COMBINED")
print("=" * 80)
if all_2026:
    portfolio = pd.concat(all_2026, ignore_index=True)
    summarize(portfolio, "ALL 3 SYMBOLS")

print("\n" + "=" * 80)
print("VERGLEICH: 2021-2025 DURCHSCHNITT PRO SYMBOL")
print("=" * 80)
for sym in SYMBOLS:
    try:
        df = pd.read_csv(f"results/trades_{sym}.csv")
    except FileNotFoundError:
        continue
    df[TCOL] = pd.to_datetime(df[TCOL])
    pre = df[df[TCOL] < "2026-01-01"]
    if len(pre) == 0:
        continue
    years = pre[TCOL].dt.year.nunique()
    n_y = len(pre) / years
    r_y = pre[RCOL].sum() / years
    wr = (pre[RCOL] > 0).sum() / len(pre) * 100
    print(f"  {sym}: {n_y:5.0f} Trades/Jahr  sumR/Jahr {r_y:+7.2f}  WR {wr:.1f}%")

print("\n" + "=" * 80)
print("2026 PACE-RATE (annualisiert aus ~3.5 Monaten)")
print("=" * 80)
# 2026 lief von ca. 01-01 bis 04-20 = ca. 110 Tage = 0.30 Jahre
# Hochrechnung
if all_2026:
    portfolio = pd.concat(all_2026, ignore_index=True)
    portfolio[TCOL] = pd.to_datetime(portfolio[TCOL])
    if len(portfolio) > 0:
        span_days = (portfolio[TCOL].max() - pd.Timestamp("2026-01-01")).days + 1
        scale = 365.25 / max(span_days, 1)
        n_annual = len(portfolio) * scale
        r_annual = portfolio[RCOL].sum() * scale
        print(f"  2026 lief: {span_days} Tage  (Scaling x{scale:.2f})")
        print(f"  Hochgerechnet: {n_annual:.0f} Trades/Jahr  sumR/Jahr {r_annual:+.2f}")
