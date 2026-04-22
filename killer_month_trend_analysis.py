#!/usr/bin/env python3
"""
killer_month_trend_analysis.py
--------------------------------
Pruefe, ob die Loss-Trades in den Killer-Monaten systematisch Counter-Trend
zur H4-Richtung waren. Wenn ja, koennen wir einen Filter bauen, der diese
Trades skippt und DD strukturell reduziert - OHNE das Sizing anzufassen.

Indikatoren (am Entry-Zeitpunkt, H4-Timeframe):
  - EMA20(H4), EMA50(H4)         -> Trend-Richtung
  - EMA20-Slope (5 H4-Bars diff) -> Trend-Staerke
  - ADX(14) auf H4               -> Trend-Intensitaet
  - Distance Close vs EMA20 (in ATR) -> Extended-Price Warning

Definitionen:
  Trend-UP   = EMA20 > EMA50 AND EMA20-Slope > 0
  Trend-DN   = EMA20 < EMA50 AND EMA20-Slope < 0
  Counter-Trend = (direction=short & Trend-UP) OR (direction=long & Trend-DN)
  Strong-Trend  = ADX(H4) >= 25
  Strong-CT    = Counter-Trend AND Strong-Trend  <- Filter-Kandidat

Usage:
  python3 killer_month_trend_analysis.py
  python3 killer_month_trend_analysis.py --data-dir data --trades-dir results
"""

import argparse
import sys
from pathlib import Path
import numpy as np
import pandas as pd

SYMBOLS_DEFAULT = ["EURUSD", "XAUUSD", "USDJPY"]

KILLER_MONTHS = [
    "2024-06", "2024-04", "2024-02",     # DD #1
    "2023-04", "2023-08",                # DD #2
    "2021-08", "2021-10",                # DD #3
    "2025-10", "2026-01",                # DD #4
    "2022-11", "2022-12",                # DD #5
]


def load_price(symbol: str, data_dir: str) -> pd.DataFrame:
    """Lade M15-Preise. Probiere diverse Dateinamen-Varianten."""
    candidates = [
        f"{symbol}_M15.csv", f"{symbol}.csv",
        f"{symbol.lower()}_M15.csv", f"{symbol.lower()}.csv",
        f"{symbol}_15min.csv", f"{symbol}_m15.csv",
    ]
    paths = [Path(data_dir) / c for c in candidates]
    path = next((p for p in paths if p.exists()), None)
    if path is None:
        raise FileNotFoundError(
            f"Keine Preis-CSV fuer {symbol} in '{data_dir}'. "
            f"Probiert: {[p.name for p in paths]}"
        )
    df = pd.read_csv(path)
    print(f"  [DEBUG] {symbol} CSV path: {path}")
    print(f"  [DEBUG] {symbol} Columns: {list(df.columns)}")
    print(f"  [DEBUG] {symbol} dtypes: {df.dtypes.to_dict()}")
    print(f"  [DEBUG] {symbol} First row: {df.iloc[0].to_dict()}")

    # Zeit-Spalte finden
    time_col = None
    for c in df.columns:
        if c.lower() in ("time", "datetime", "date", "timestamp", "entry_time"):
            time_col = c
            break
    if time_col is None:
        time_col = df.columns[0]
    print(f"  [DEBUG] {symbol} Time column detected: '{time_col}'")

    # Auto-detect Unix-Timestamp Unit (ms/s/us/ns)
    if pd.api.types.is_numeric_dtype(df[time_col]):
        sample = float(df[time_col].dropna().iloc[0])
        if   sample > 1e17: unit = "ns"
        elif sample > 1e14: unit = "us"
        elif sample > 1e11: unit = "ms"
        else:               unit = "s"
        print(f"  [DEBUG] {symbol} Numeric timestamp detected, unit='{unit}' (sample={sample:.0f})")
        parsed = pd.to_datetime(df[time_col], unit=unit, errors="coerce")
    else:
        parsed = pd.to_datetime(df[time_col], errors="coerce")
    n_fail = parsed.isna().sum()
    
    if n_fail > 0:
        print(f"  [WARN] {n_fail} Zeit-Werte konnten nicht geparst werden")
    df["time"] = parsed.astype("datetime64[ns]")
    print(f"  [DEBUG] {symbol} Parsed time range: {df['time'].min()} -> {df['time'].max()}")

    # OHLC normalisieren
    rename_map = {}
    for c in list(df.columns):
        if c == "time":
            continue
        lc = c.lower()
        if lc in ("open", "o"):     rename_map[c] = "open"
        elif lc in ("high", "h"):   rename_map[c] = "high"
        elif lc in ("low", "l"):    rename_map[c] = "low"
        elif lc in ("close", "c"):  rename_map[c] = "close"
    df = df.rename(columns=rename_map)

    missing = {"open", "high", "low", "close"} - set(df.columns)
    if missing:
        raise KeyError(f"{path}: fehlende OHLC-Spalten {missing}. Vorhanden: {list(df.columns)}")

    return df[["time", "open", "high", "low", "close"]] \
             .dropna(subset=["time"]) \
             .sort_values("time").reset_index(drop=True)


def resample_h4(m15: pd.DataFrame) -> pd.DataFrame:
    df = m15.set_index("time")
    h4 = df.resample("4h").agg({
        "open": "first", "high": "max",
        "low": "min",    "close": "last",
    }).dropna().reset_index()
    return h4


def compute_atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def compute_adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    up = high.diff()
    dn = -low.diff()
    plus_dm  = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([high - low, (high - close.shift(1)).abs(),
                    (low  - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(n, min_periods=n).mean()
    plus_di  = 100 * pd.Series(plus_dm).rolling(n, min_periods=n).mean() / atr
    minus_di = 100 * pd.Series(minus_dm).rolling(n, min_periods=n).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.rolling(n, min_periods=n).mean()


def enrich(trades: pd.DataFrame, m15: pd.DataFrame, symbol: str = "") -> pd.DataFrame:
    trades = trades.copy()
    trades["entry_time"] = pd.to_datetime(trades["entry_time"]).astype("datetime64[ns]")

    h4 = resample_h4(m15)
    print(f"  [DEBUG {symbol}] H4-Bars: {len(h4)}  range: {h4['time'].min()} -> {h4['time'].max()}")

    # Wilder-Smoothing via ewm(alpha=1/n) - robuster als rolling.mean()
    n = 14
    high, low, close = h4["high"], h4["low"], h4["close"]
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    h4["atr14"] = tr.ewm(alpha=1/n, adjust=False, min_periods=n).mean()

    up = high.diff()
    dn = -low.diff()
    plus_dm  = pd.Series(np.where((up > dn) & (up > 0), up.fillna(0), 0.0), index=h4.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn.fillna(0), 0.0), index=h4.index)
    plus_di  = 100 * plus_dm.ewm(alpha=1/n, adjust=False, min_periods=n).mean() / h4["atr14"]
    minus_di = 100 * minus_dm.ewm(alpha=1/n, adjust=False, min_periods=n).mean() / h4["atr14"]
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    h4["adx14"] = dx.ewm(alpha=1/n, adjust=False, min_periods=n).mean()

    h4["ema20"]   = h4["close"].ewm(span=20, adjust=False).mean()
    h4["ema50"]   = h4["close"].ewm(span=50, adjust=False).mean()
    h4["ema_slope"] = h4["ema20"].diff(5)
    h4["dist_ema20_atr"] = (h4["close"] - h4["ema20"]) / h4["atr14"]
    h4["time"] = pd.to_datetime(h4["time"]).astype("datetime64[ns]")

    # DEBUG: pruefen ob Indikatoren berechnet wurden
    non_nan = h4["adx14"].notna().sum()
    print(f"  [DEBUG {symbol}] adx14 non-NaN: {non_nan}/{len(h4)}")
    print(f"  [DEBUG {symbol}] ema20 non-NaN: {h4['ema20'].notna().sum()}/{len(h4)}")
    if non_nan > 0:
        idx_first_ok = h4["adx14"].first_valid_index()
        print(f"  [DEBUG {symbol}] First non-NaN adx14 row:")
        print(h4.loc[idx_first_ok:idx_first_ok+1, ["time","close","ema20","ema50","adx14"]].to_string())
        print(f"  [DEBUG {symbol}] Last H4 row:")
        print(h4.tail(1)[["time","close","ema20","ema50","adx14"]].to_string())

    trades = trades.sort_values("entry_time").reset_index(drop=True)
    h4     = h4.sort_values("time").reset_index(drop=True)

    m = pd.merge_asof(
        trades,
        h4[["time","atr14","adx14","ema20","ema50","ema_slope","dist_ema20_atr"]],
        left_on="entry_time", right_on="time", direction="backward",
    )

    non_nan_after = m["adx14"].notna().sum()
    print(f"  [DEBUG {symbol}] After merge: {non_nan_after}/{len(m)} trades have adx14")
    if non_nan_after > 0:
        print(f"  [DEBUG {symbol}] Sample merged trades:")
        print(m.head(3)[["entry_time","direction","time","ema20","ema50","adx14"]].to_string())

    m["trend_up"] = (m["ema20"] > m["ema50"]) & (m["ema_slope"] > 0)
    m["trend_dn"] = (m["ema20"] < m["ema50"]) & (m["ema_slope"] < 0)
    m["counter_trend"] = (
        ((m["direction"].str.lower() == "short") & m["trend_up"]) |
        ((m["direction"].str.lower() == "long")  & m["trend_dn"])
    )
    m["strong_trend"] = m["adx14"] >= 25
    m["strong_ct"]    = m["counter_trend"] & m["strong_trend"]

    print(f"  [DEBUG {symbol}] Trend-UP bars: {m['trend_up'].sum()}  Trend-DN: {m['trend_dn'].sum()}  "
          f"Counter-Trend: {m['counter_trend'].sum()}  Strong-CT: {m['strong_ct'].sum()}")
    return m


def stats(df: pd.DataFrame, label: str):
    n = len(df)
    if n == 0:
        print(f"  {label:<32} n=0")
        return
    w = (df["total_r"] > 0).sum()
    l = (df["total_r"] <= 0).sum()
    s = df["total_r"].sum()
    avg = df["total_r"].mean()
    wr = 100 * w / n if n else 0
    pos = df[df["total_r"] > 0]["total_r"].sum()
    neg = -df[df["total_r"] <= 0]["total_r"].sum()
    pf  = pos / neg if neg > 0 else float("inf")
    print(f"  {label:<32} n={n:5d}  W={w:4d}  L={l:4d}  "
          f"WR={wr:5.1f}%  sumR={s:+8.2f}  avgR={avg:+.3f}  PF={pf:.2f}")


def bucket(df: pd.DataFrame, col: str, bins, title: str):
    print(f"\n>> {title}")
    d = df.dropna(subset=[col]).copy()
    d["bucket"] = pd.cut(d[col], bins=bins, include_lowest=True)
    g = d.groupby("bucket", observed=True).agg(
        n=("total_r", "size"),
        wins=("total_r", lambda s: (s > 0).sum()),
        sumR=("total_r", "sum"),
        avgR=("total_r", "mean"),
    )
    g["WR%"] = (100 * g["wins"] / g["n"]).round(1)
    print(g.round(3).to_string())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir",   default="data")
    ap.add_argument("--trades-dir", default="results")
    ap.add_argument("--symbols",    nargs="+", default=SYMBOLS_DEFAULT)
    ap.add_argument("--out-csv",    default="results/killer_trend_enriched.csv")
    args = ap.parse_args()

    print("=" * 82)
    print("KILLER-MONTH TREND ANALYSIS")
    print("=" * 82)
    print(f"  Data-Dir:    {args.data_dir}")
    print(f"  Trades-Dir:  {args.trades_dir}")
    print(f"  Symbols:     {args.symbols}")
    print(f"  Killer-Mths: {KILLER_MONTHS}")

    all_dfs = []
    for sym in args.symbols:
        tpath = Path(args.trades_dir) / f"trades_{sym}.csv"
        if not tpath.exists():
            print(f"[SKIP] {tpath} fehlt")
            continue

        print(f"\n---------------- {sym} ----------------")
        trades = pd.read_csv(tpath)
        if "entry_time" not in trades.columns:
            print(f"  [WARN] Keine entry_time-Spalte. Vorhanden: {list(trades.columns)}")
            continue
        trades["symbol"] = sym
        trades["entry_time"] = pd.to_datetime(trades["entry_time"])
        trades["month"] = trades["entry_time"].dt.to_period("M").astype(str)
        print(f"  Trades: {len(trades)}  ({trades['entry_time'].min()} -> {trades['entry_time'].max()})")

        try:
            price = load_price(sym, args.data_dir)
        except (FileNotFoundError, KeyError) as e:
            print(f"  [ERR] {e}")
            continue
        print(f"  Price:  {len(price)} M15-Bars")

        enriched = enrich(trades, price, symbol=sym)
        all_dfs.append(enriched)

        killer = enriched[enriched["month"].isin(KILLER_MONTHS)]
        normal = enriched[~enriched["month"].isin(KILLER_MONTHS)]
        stats(killer, f"{sym} Killer-Monate")
        stats(normal, f"{sym} Normal-Monate")

    if not all_dfs:
        print("\nKeine Daten geladen. Ende.")
        return 1

    comb = pd.concat(all_dfs, ignore_index=True)

    Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
    comb.to_csv(args.out_csv, index=False)

    print("\n" + "=" * 82)
    print("KOMBINIERT - ALLE SYMBOLE")
    print("=" * 82)
    killer = comb[comb["month"].isin(KILLER_MONTHS)]
    normal = comb[~comb["month"].isin(KILLER_MONTHS)]

    stats(comb,   "GESAMT (alle Trades)")
    stats(killer, "KILLER-MONATE")
    stats(normal, "NORMAL-MONATE")

    print("\n" + "=" * 82)
    print("COUNTER-TREND AUFTEILUNG")
    print("=" * 82)
    stats(comb[comb["counter_trend"]],  "ALL  - Counter-Trend")
    stats(comb[~comb["counter_trend"]], "ALL  - With-Trend")
    stats(killer[killer["counter_trend"]],  "KILL - Counter-Trend")
    stats(killer[~killer["counter_trend"]], "KILL - With-Trend")
    stats(normal[normal["counter_trend"]],  "NORM - Counter-Trend")
    stats(normal[~normal["counter_trend"]], "NORM - With-Trend")

    print("\n" + "=" * 82)
    print("STRONG-COUNTER-TREND (= CT + ADX>=25)  <-- FILTER-KANDIDAT")
    print("=" * 82)
    stats(comb[comb["strong_ct"]],        "ALL  - Strong-CT")
    stats(comb[~comb["strong_ct"]],       "ALL  - NOT Strong-CT")
    stats(killer[killer["strong_ct"]],    "KILL - Strong-CT")
    stats(killer[~killer["strong_ct"]],   "KILL - NOT Strong-CT")
    stats(normal[normal["strong_ct"]],    "NORM - Strong-CT")
    stats(normal[~normal["strong_ct"]],   "NORM - NOT Strong-CT")

    print("\n" + "=" * 82)
    print("ADX(H4) BUCKETS - ALL TRADES")
    print("=" * 82)
    bucket(comb, "adx14", [0, 15, 20, 25, 30, 40, 100], "ADX-H4 (gesamt)")

    print("\n" + "=" * 82)
    print("ADX(H4) BUCKETS - KILLER-MONATE")
    print("=" * 82)
    bucket(killer, "adx14", [0, 15, 20, 25, 30, 40, 100], "ADX-H4 (killer)")

    print("\n" + "=" * 82)
    print("ADX(H4) BUCKETS - NUR COUNTER-TREND")
    print("=" * 82)
    bucket(comb[comb["counter_trend"]], "adx14",
           [0, 15, 20, 25, 30, 40, 100], "ADX-H4 (counter-trend only)")

    print("\n" + "=" * 82)
    print("STRONG-CT - PER SYMBOL")
    print("=" * 82)
    for sym in comb["symbol"].unique():
        sub = comb[(comb["symbol"] == sym) & comb["strong_ct"]]
        stats(sub, f"{sym} Strong-CT")

    print("\n" + "=" * 82)
    print("FILTER-SIMULATION:  SKIP ALL STRONG-COUNTER-TREND")
    print("=" * 82)
    kept    = comb[~comb["strong_ct"]]
    skipped = comb[comb["strong_ct"]]
    print(f"  Skipped Trades:      {len(skipped)}  ({100*len(skipped)/len(comb):.1f}% der Gesamt)")
    print(f"  Net-R der Skips:     {skipped['total_r'].sum():+.2f} R")
    print(f"    (negativ = Filter sparte R; positiv = Filter verpasste R)")
    print()
    stats(comb, "VORHER - Alle Trades")
    stats(kept, "NACHHER - nach Filter")

    print("\n" + "=" * 82)
    print("KILLER-MONATE: VORHER vs NACH FILTER")
    print("=" * 82)
    for month in KILLER_MONTHS:
        before = killer[killer["month"] == month]
        after  = before[~before["strong_ct"]]
        if len(before) == 0:
            continue
        b_sum = before["total_r"].sum()
        a_sum = after["total_r"].sum()
        n_before = len(before)
        n_after = len(after)
        skip = n_before - n_after
        print(f"  {month}: VORHER n={n_before:3d} sumR={b_sum:+7.2f}  |  "
              f"NACHHER n={n_after:3d} sumR={a_sum:+7.2f}  |  skip={skip:2d}  "
              f"delta={a_sum-b_sum:+.2f}R")

    print(f"\n[OK] Enriched CSV: {args.out_csv}")
    print("\nFazit-Check:")
    print("  - Ist KILL-Strong-CT deutlich schlechter als KILL-NOT-Strong-CT?")
    print("  - Ist NORM-Strong-CT auch negativ, oder nur in Killer-Monaten?")
    print("  - Wenn Skips in Killer-Monaten viel R retten & in Normal-Monaten nichts kosten")
    print("    -> Filter in setup_finder.py einbauen, re-backtest")
    return 0


if __name__ == "__main__":
    sys.exit(main())