#!/usr/bin/env python3
"""
macro_refresh.py  (Strategie 2 — Live-Datenpflege fuer das Regime-Overlay)
==========================================================================

Haelt data/macro/ aktuell, damit regime_overlay.build_regime() im Live-
Betrieb korrekte (frische) Signale liefert. Zwei Teile:

  1) FRED-CSVs herunterladen (US/JP-Renditen, VIX, USD-Index)
     -> data/macro/<ID>.csv   (volle Historie ab 2010)
  2) Aktuelle Tages-Closes fuer USDJPY/XAUUSD/EURUSD aus MT5 schreiben
     -> data/macro/_daily_px.csv  (wird von build_regime an das statische
        Parquet angehaengt -> Proxy bleibt aktuell)

LAEUFT AUF DEM VPS (Internet + MT5 noetig). NICHT in der Sandbox testbar.
Taeglich per Scheduler ausfuehren, z.B. Windows:
    schtasks /Create /SC DAILY /ST 06:00 /TN smc_macro_refresh ^
        /TR "python C:\\pfad\\macro_refresh.py"

Beide Teile sind unabhaengig und fail-soft: faellt einer aus, bleibt der
letzte Stand erhalten (build_regime ist fail-open).

Usage:
    python macro_refresh.py                # beide Teile
    python macro_refresh.py --fred-only
    python macro_refresh.py --px-only
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.request

MACRO_DIR = "data/macro"
FRED_SERIES = ["DGS10", "DGS2", "VIXCLS", "DTWEXBGS", "IRLTLT01JPM156N"]
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}&cosd=2010-01-01"

# Symbole + Broker-Suffix-Aufloesung uebernimmt live_feed; hier nur Prefixe.
PX_SYMBOLS = ["USDJPY", "XAUUSD", "EURUSD"]
PX_PATH = os.path.join(MACRO_DIR, "_daily_px.csv")
PX_DAYS = 400          # genug fuer die 200-Tage-SMA + Puffer


def refresh_fred() -> int:
    os.makedirs(MACRO_DIR, exist_ok=True)
    ok = 0
    for sid in FRED_SERIES:
        url = FRED_URL.format(id=sid)
        dest = os.path.join(MACRO_DIR, f"{sid}.csv")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            if not data or b"," not in data.splitlines()[0]:
                raise ValueError("unerwartetes Format")
            with open(dest, "wb") as f:
                f.write(data)
            print(f"  [FRED] {sid}: {len(data.splitlines())-1} Zeilen -> {dest}")
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"  [FRED] {sid}: FEHLER {e} (alter Stand bleibt)")
    return ok


def refresh_px() -> bool:
    """Schreibt aktuelle Tages-Closes aus MT5 nach _daily_px.csv."""
    try:
        import pandas as pd
        import live_feed  # nutzt dieselbe MT5-Anbindung wie der Live-Runner
    except Exception as e:  # noqa: BLE001
        print(f"  [PX] live_feed/MT5 nicht verfuegbar ({e}) -> uebersprungen")
        return False
    try:
        live_feed.ensure_initialized()
        cols = {}
        for pre in PX_SYMBOLS:
            # M15-Frames holen und auf Tages-Close verdichten (Parität zu daily_close)
            name, ltf, htf = live_feed.get_strategy_frames(pre, n_bars=PX_DAYS * 96)
            daily = ltf["Close"].resample("1D").last().dropna()
            cols[pre] = daily
        df = pd.DataFrame(cols).dropna(how="all")
        df.index.name = "date"
        os.makedirs(MACRO_DIR, exist_ok=True)
        df.tail(PX_DAYS).to_csv(PX_PATH)
        print(f"  [PX] {len(df.tail(PX_DAYS))} Tage -> {PX_PATH} "
              f"(bis {df.index.max().date()})")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  [PX] FEHLER {e} (alter Stand bleibt)")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Regime-Overlay Makro-Refresh (VPS)")
    ap.add_argument("--fred-only", action="store_true")
    ap.add_argument("--px-only", action="store_true")
    args = ap.parse_args()

    print("== macro_refresh ==")
    if not args.px_only:
        n = refresh_fred()
        print(f"FRED: {n}/{len(FRED_SERIES)} Serien aktualisiert.")
    if not args.fred_only:
        refresh_px()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
