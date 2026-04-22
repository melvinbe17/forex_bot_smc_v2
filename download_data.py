"""
download_data.py
----------------
Laedt GBPUSD und XAUUSD M15-Daten von Dukascopy und speichert sie
im gleichen Format wie data/EURUSD_M15.csv:
    timestamp,open,high,low,close
wobei timestamp = Unix-Millisekunden (UTC).

Verwendung:
    pip3 install dukascopy-python --break-system-packages
    python3 download_data.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pandas as pd

try:
    import dukascopy_python
except ImportError:
    print("FEHLER: dukascopy-python ist nicht installiert.")
    print("Bitte ausfuehren:")
    print("    pip3 install dukascopy-python --break-system-packages")
    sys.exit(1)

try:
    import dukascopy_python.instruments as _inst
except ImportError as e:
    print(f"FEHLER: dukascopy_python.instruments nicht importierbar: {e}")
    sys.exit(1)


def _find_instrument(*needles: str):
    """
    Sucht in dukascopy_python.instruments nach einer INSTRUMENT_*-Konstante,
    deren Name alle needles (case-insensitive) enthaelt.
    Gibt (value, name) zurueck. Bricht mit Liste aller Konstanten ab,
    wenn nichts gefunden wird.
    """
    names = [n for n in dir(_inst) if n.startswith("INSTRUMENT")]
    matches = [
        n for n in names
        if all(needle.lower() in n.lower() for needle in needles)
    ]
    if not matches:
        print(f"\nFEHLER: Kein Instrument gefunden fuer {needles}.")
        print("Verfuegbare Instrument-Konstanten:")
        for n in names:
            print(f"  {n}")
        sys.exit(1)
    # Kuerzester Name = wahrscheinlich die Hauptkonstante (keine Exoten)
    matches.sort(key=len)
    chosen = matches[0]
    return getattr(_inst, chosen), chosen


# --- Konfiguration ----------------------------------------------------------

START = datetime(2021, 1, 1, tzinfo=timezone.utc)
END   = datetime(2026, 4, 21, tzinfo=timezone.utc)

DATA_DIR = "data"

_gbpusd, _gbpusd_name = _find_instrument("gbp", "usd")
_xauusd, _xauusd_name = _find_instrument("xau", "usd")
_usdjpy, _usdjpy_name = _find_instrument("usd", "jpy")
_nas100, _nas100_name = _find_instrument("nq", "100")

print(f"[INFO] GBPUSD -> {_gbpusd_name}")
print(f"[INFO] XAUUSD -> {_xauusd_name}")
print(f"[INFO] USDJPY -> {_usdjpy_name}")
print(f"[INFO] NAS100 -> {_nas100_name}")

SYMBOLS = [
    ("GBPUSD", _gbpusd),
    ("XAUUSD", _xauusd),
    ("USDJPY", _usdjpy),
    ("NAS100", _nas100),
]


# --- Download ---------------------------------------------------------------

def to_eurusd_format(df: pd.DataFrame) -> pd.DataFrame:
    """
    Mappt dukascopy-python-Output auf das Format von EURUSD_M15.csv:
        timestamp (Unix-ms),open,high,low,close
    dukascopy-python liefert meist einen DatetimeIndex + OHLC-Spalten.
    """
    # Spalten robust auswaehlen (manche Versionen: open/high/low/close,
    # andere: bidOpen/bidHigh/...)
    col_map = {}
    for target, candidates in [
        ("open",  ["open",  "bidOpen",  "Open",  "o"]),
        ("high",  ["high",  "bidHigh",  "High",  "h"]),
        ("low",   ["low",   "bidLow",   "Low",   "l"]),
        ("close", ["close", "bidClose", "Close", "c"]),
    ]:
        for c in candidates:
            if c in df.columns:
                col_map[target] = c
                break
        if target not in col_map:
            raise RuntimeError(
                f"Konnte Spalte '{target}' nicht finden. Verfuegbar: {list(df.columns)}"
            )

    # Index -> Unix-Millisekunden (EURUSD_M15.csv nutzt ms)
    if not isinstance(df.index, pd.DatetimeIndex):
        raise RuntimeError("Erwartet DatetimeIndex von dukascopy_python.fetch()")

    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")

    ts_ms = idx.tz_localize(None).astype("datetime64[ms]").astype("int64")

    out = pd.DataFrame({
        "timestamp": ts_ms,
        "open":  df[col_map["open"]].values,
        "high":  df[col_map["high"]].values,
        "low":   df[col_map["low"]].values,
        "close": df[col_map["close"]].values,
    })
    return out


def main() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)

    for label, instrument in SYMBOLS:
        out_path = os.path.join(DATA_DIR, f"{label}_M15.csv")

        if os.path.exists(out_path):
            print(f"[SKIP] {out_path} existiert schon "
                  "(zum Neuladen bitte loeschen).")
            continue

        print(f"[LADE] {label} M15  {START.date()} -> {END.date()} ...")
        try:
            df = dukascopy_python.fetch(
                instrument,
                dukascopy_python.INTERVAL_MIN_15,
                dukascopy_python.OFFER_SIDE_BID,
                START,
                END,
            )
        except Exception as e:
            print(f"[FEHLER] Download fuer {label} fehlgeschlagen: {e}")
            continue

        if df is None or len(df) == 0:
            print(f"[FEHLER] Keine Daten fuer {label} erhalten.")
            continue

        out = to_eurusd_format(df)
        out.to_csv(out_path, index=False)

        first_ts = datetime.fromtimestamp(out["timestamp"].iloc[0]  / 1000, tz=timezone.utc)
        last_ts  = datetime.fromtimestamp(out["timestamp"].iloc[-1] / 1000, tz=timezone.utc)
        print(f"[OK]   {out_path}  ({len(out)} Bars, "
              f"{first_ts} -> {last_ts})")

    print("\nFertig. Als Naechstes:")
    print("    python3 backtest_m15.py --symbol GBPUSD --limit 0")
    print("    python3 backtest_m15.py --symbol XAUUSD --limit 0")


if __name__ == "__main__":
    main()