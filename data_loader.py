"""
data_loader.py  (forex_bot_smc)
===============================

Laedt OHLC-Daten fuer Multi-Timeframe-SMC-Analysen.

Unterstuetzt zwei Quellen:
  1) yfinance  - schnelles Prototyping, aber M15 nur ~60 Tage zurueck
  2) CSV-Files - MT5 / Dukascopy Export fuer ernsthafte Backtests
                 (3-5 Jahre M15 moeglich)

CSV-Format (erwartet):
    datetime,open,high,low,close,volume
    2023-01-02 09:00:00,1.0712,1.0725,1.0710,1.0722,12345
    ...

Dateiname-Konvention (in data/):
    {SYMBOL}_{TIMEFRAME}.csv
    z.B. EURUSD_M15.csv, US500_H1.csv
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

import config


# ----------------------------------------------------------------------
# Helper: Timeframe-Konvertierung
# ----------------------------------------------------------------------
# yfinance-Intervall-Strings
_YF_INTERVAL = {
    "M1":  "1m",
    "M5":  "5m",
    "M15": "15m",
    "M30": "30m",
    "H1":  "1h",
    "H4":  None,     # nicht direkt, wird aus H1 resampled
    "D1":  "1d",
}

# pandas-resample-Freq
_PD_FREQ = {
    "M1":  "1min",
    "M5":  "5min",
    "M15": "15min",
    "M30": "30min",
    "H1":  "1h",
    "H4":  "4h",
    "D1":  "1D",
}


# ----------------------------------------------------------------------
# CSV-Loader
# ----------------------------------------------------------------------
def _try_parse_datetime(series: pd.Series) -> pd.Series:
    """Probiert alle Formate aus config.CSV_DATETIME_FORMATS durch.

    Zusaetzlich: erkennt Dukascopy-Node-Style numerische Timestamps
    (Millisekunden seit Epoch) und konvertiert sie korrekt. Ohne diese
    Extra-Logik wuerde pandas die Zahlen als Nanosekunden lesen und
    alle Bars in 1970-01-01 landen.
    """
    # (1) Numerische Timestamps -> ms-/s-Epoch erkennen.
    #   dukascopy-node CSV: "1609459200000" (ms) in Spalte timestamp.
    if pd.api.types.is_numeric_dtype(series):
        non_null = series.dropna()
        if len(non_null) > 0:
            sample = float(non_null.iloc[0])
            if sample > 1e14:       # Mikrosekunden
                return pd.to_datetime(series, unit="us", errors="coerce")
            if sample > 1e11:       # Millisekunden (Dukascopy default)
                return pd.to_datetime(series, unit="ms", errors="coerce")
            if sample > 1e9:        # Sekunden
                return pd.to_datetime(series, unit="s", errors="coerce")

    # (2) String-basierte Spalte, aber Werte sind in Wirklichkeit
    #     numerische Strings ("1609459200000"). Erst Auto-Parse versuchen,
    #     dann pruefen ob Ergebnis bei 1970 landet -> dann als ms lesen.
    try:
        result = pd.to_datetime(series, errors="raise")
        first_valid = result.dropna()
        if len(first_valid) > 0 and first_valid.iloc[0].year < 2000:
            try:
                as_num = pd.to_numeric(series, errors="raise")
                sample = float(as_num.dropna().iloc[0])
                if sample > 1e11:
                    return pd.to_datetime(as_num, unit="ms",
                                          errors="coerce")
                if sample > 1e9:
                    return pd.to_datetime(as_num, unit="s",
                                          errors="coerce")
            except Exception:
                pass
        return result
    except Exception:
        pass

    # (3) Explizite Formatliste durchprobieren
    for fmt in config.CSV_DATETIME_FORMATS:
        try:
            return pd.to_datetime(series, format=fmt, errors="raise")
        except Exception:
            continue

    # (4) Letzter Fallback: errors="coerce"
    return pd.to_datetime(series, errors="coerce")

def load_csv(symbol: str, timeframe: str,
             data_dir: Optional[str] = None) -> pd.DataFrame:
    """Laedt eine CSV-Datei aus data_dir/{symbol}_{timeframe}.csv."""
    if data_dir is None:
        data_dir = config.DATA_DIR
    path = Path(data_dir) / f"{symbol}_{timeframe}.csv"
    if not path.exists():
        raise FileNotFoundError(f"CSV nicht gefunden: {path}")

    df = pd.read_csv(path)

    # Spalten normalisieren
    df.columns = [c.strip().lower() for c in df.columns]

    # Haeufige Aliase
    rename_map = {
        "date": "datetime", "time": "datetime", "timestamp": "datetime",
        "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume",
        "vol": "volume", "tickvol": "volume",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items()
                            if k in df.columns})

    required = ["datetime", "open", "high", "low", "close"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"{path}: fehlende Spalten {missing}. "
            f"Erwartet: datetime,open,high,low,close[,volume]. "
            f"Gefunden: {list(df.columns)}"
        )

    df["datetime"] = _try_parse_datetime(df["datetime"])
    df = df.dropna(subset=["datetime"]).sort_values("datetime")
    df = df.set_index("datetime")

    # Cap-Column-Style (SMC-Modul erwartet Open/High/Low/Close)
    df = df.rename(columns={
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume",
    })
    if "Volume" not in df.columns:
        df["Volume"] = 0

    # Nur OHLCV-Spalten behalten
    df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    return df


# ----------------------------------------------------------------------
# yfinance-Loader  (nur fuer Prototyping)
# ----------------------------------------------------------------------
def load_yfinance(symbol: str, timeframe: str,
                  lookback_days: Optional[int] = None) -> pd.DataFrame:
    """Laedt OHLC von yfinance. TF "H4" wird aus H1 resampled."""
    try:
        import yfinance as yf
    except ImportError as e:
        raise ImportError(
            "yfinance nicht installiert. `pip install yfinance`"
        ) from e

    meta = config.INSTRUMENTS.get(symbol)
    if meta is None:
        raise KeyError(f"Unbekanntes Symbol: {symbol}")
    ticker = meta["ticker"]

    # H4 -> H1 holen, danach resamplen
    fetch_tf = "H1" if timeframe == "H4" else timeframe
    interval = _YF_INTERVAL.get(fetch_tf)
    if interval is None:
        raise ValueError(f"Timeframe nicht unterstuetzt: {timeframe}")

    if lookback_days is None:
        lookback_days = config.YF_LOOKBACK_DAYS.get(fetch_tf, 60)

    # yfinance-period-String bauen
    period = f"{lookback_days}d"

    df = yf.download(ticker, period=period, interval=interval,
                     progress=False, auto_adjust=False)
    if df.empty:
        raise ValueError(
            f"yfinance gab leeres DF zurueck fuer {symbol} ({ticker}) "
            f"@{interval}/{period}"
        )

    # MultiIndex-Columns flattenen (yfinance neuere Versionen)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    # Nur OHLCV
    cols = ["Open", "High", "Low", "Close", "Volume"]
    df = df[[c for c in cols if c in df.columns]].copy()
    if "Volume" not in df.columns:
        df["Volume"] = 0
    df = df.dropna()

    # Optional auf H4 resamplen
    if timeframe == "H4":
        df = resample(df, "H4")

    return df


# ----------------------------------------------------------------------
# Resample  (z.B. M15 -> H1 fuer HTF-Bias)
# ----------------------------------------------------------------------
def resample(df: pd.DataFrame, target_tf: str) -> pd.DataFrame:
    """Standard OHLCV-Resample. df muss DatetimeIndex haben."""
    freq = _PD_FREQ.get(target_tf)
    if freq is None:
        raise ValueError(f"Resample-TF nicht unterstuetzt: {target_tf}")
    agg = {
        "Open": "first", "High": "max", "Low": "min",
        "Close": "last", "Volume": "sum",
    }
    out = df.resample(freq, label="right", closed="right").agg(agg)
    return out.dropna()


# ----------------------------------------------------------------------
# High-Level: Laedt pro Symbol ein dict mit allen gebrauchten TFs
# ----------------------------------------------------------------------
def load_symbol(symbol: str,
                timeframes: Optional[list[str]] = None,
                source: Optional[str] = None) -> Dict[str, pd.DataFrame]:
    """
    Gibt dict {tf: df} zurueck, z.B. {"H1": ..., "M15": ...}.
    source: "csv", "yfinance" oder "auto" (CSV bevorzugt, Fallback yf).
    """
    if timeframes is None:
        timeframes = [config.HTF_TIMEFRAME, config.LTF_TIMEFRAME]
    if source is None:
        source = config.DATA_SOURCE

    out: Dict[str, pd.DataFrame] = {}
    for tf in timeframes:
        df = None
        last_err: Optional[Exception] = None

        if source in ("csv", "auto"):
            try:
                df = load_csv(symbol, tf)
            except FileNotFoundError as e:
                last_err = e
                if source == "csv":
                    raise

        if df is None and source in ("yfinance", "auto"):
            try:
                df = load_yfinance(symbol, tf)
            except Exception as e:  # noqa: BLE001
                last_err = e

        if df is None:
            raise RuntimeError(
                f"Konnte {symbol}/{tf} nicht laden (source={source}). "
                f"Letzter Fehler: {last_err}"
            )
        out[tf] = df

    return out


def load_portfolio(symbols: Optional[list[str]] = None,
                   timeframes: Optional[list[str]] = None,
                   source: Optional[str] = None
                   ) -> Dict[str, Dict[str, pd.DataFrame]]:
    """
    Laedt alle konfigurierten (oder uebergebenen) Symbole in allen
    angeforderten TFs. Rueckgabe: {symbol: {tf: df}}.
    """
    if symbols is None:
        symbols = list(config.active_instruments().keys())

    out = {}
    for sym in symbols:
        try:
            out[sym] = load_symbol(sym, timeframes=timeframes, source=source)
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] Skipping {sym}: {e}")
    return out


# ----------------------------------------------------------------------
# CLI-Utility
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(
        description="Test/Download von OHLC-Daten (yfinance/CSV)."
    )
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="M15")
    ap.add_argument("--source", default="auto",
                    choices=["auto", "csv", "yfinance"])
    args = ap.parse_args()

    df = load_symbol(args.symbol, timeframes=[args.tf], source=args.source)[args.tf]
    print(f"{args.symbol} {args.tf}: {len(df)} Bars "
          f"({df.index[0]} -> {df.index[-1]})")
    print(df.tail(5))
