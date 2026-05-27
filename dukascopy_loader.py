"""
Dukascopy 15y M15 Data-Loader fuer forex_bot_smc
=================================================

Zieht EURUSD / USDJPY / XAUUSD M15-Kerzen von Dukascopy (gratis, tick-basiert)
ueber 15+ Jahre und speichert sie als Parquet-Cache. Kompatibel zum bestehenden
yfinance-Loader durch identisches DataFrame-Format.

Installation:
    pip install dukascopy-python pandas pyarrow

Standalone-Usage:
    python dukascopy_loader.py                   # alle 3 Symbole, 15 Jahre
    python dukascopy_loader.py --symbol EURUSD   # nur EURUSD
    python dukascopy_loader.py --years 20        # andere Tiefe
    python dukascopy_loader.py --force           # Cache ueberschreiben

Integration in data_loader.py (Beispiel):
--------------------------------------------------
    from dukascopy_loader import load_parquet

    def load_ohlcv(symbol, source="yfinance", years=15):
        if source == "dukascopy":
            return load_parquet(symbol, years=years)
        # ... bestehender yfinance-Code bleibt unveraendert
--------------------------------------------------

Danach in run_backtest.py:
    df = load_ohlcv("EURUSD", source="dukascopy")

Format des zurueckgegebenen DataFrames:
    Index:   DatetimeIndex (tz-naive UTC), Name "timestamp"
    Cols:    Open, High, Low, Close, Volume (float)

Download-Dauer (Richtwert):
    ~15-30 min pro Symbol bei 15 Jahren (je nach Verbindung).
    Dukascopy cached nach dem ersten Run in Parquet -> alle spaeteren Runs < 1 sek.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import sys
import pandas as pd

try:
    import dukascopy_python
    from dukascopy_python.instruments import (
        INSTRUMENT_FX_MAJORS_EUR_USD,
        INSTRUMENT_FX_MAJORS_USD_JPY,
        INSTRUMENT_FX_METALS_XAU_USD,
    )
except ImportError:
    sys.exit("ERROR: pip install dukascopy-python pandas pyarrow")


# -----------------------------------------------------------------
# KONFIG
# -----------------------------------------------------------------
HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "data" / "dukascopy"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = {
    "EURUSD": INSTRUMENT_FX_MAJORS_EUR_USD,
    "USDJPY": INSTRUMENT_FX_MAJORS_USD_JPY,
    "XAUUSD": INSTRUMENT_FX_METALS_XAU_USD,
}


# -----------------------------------------------------------------
# DOWNLOAD
# -----------------------------------------------------------------
def _fetch_year(symbol: str, year: int) -> pd.DataFrame:
    """Lade ein Kalenderjahr fuer ein Symbol (robuster Chunk)."""
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    return dukascopy_python.fetch(
        SYMBOLS[symbol],
        dukascopy_python.INTERVAL_MIN_15,
        dukascopy_python.OFFER_SIDE_BID,
        start,
        end,
    )


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Vereinheitliche Spalten und Index (tz-naive UTC)."""
    df = df.rename(columns={c: c.capitalize() for c in df.columns})
    wanted = ["Open", "High", "Low", "Close", "Volume"]
    df = df[[c for c in wanted if c in df.columns]]
    if "Volume" in df.columns:
        df = df[df["Volume"] > 0]            # Wochenenden / Holidays entfernen
    if df.index.tz is not None:
        df.index = df.index.tz_convert("UTC").tz_localize(None)
    df.index.name = "timestamp"
    return df


def download_symbol(symbol: str, years: int = 15, force: bool = False) -> Path:
    """Downloade oder lade Cache. Returns Path zum Parquet-File."""
    cache = CACHE_DIR / f"{symbol}_M15_{years}y.parquet"
    if cache.exists() and not force:
        size_mb = cache.stat().st_size / 1e6
        print(f"[skip]     {symbol}: cache existiert ({cache.name}, {size_mb:.1f} MB)")
        return cache

    end_year = datetime.now(timezone.utc).year
    start_year = end_year - years

    parts = []
    for y in range(start_year, end_year + 1):
        print(f"[download] {symbol} {y} ...", end="", flush=True)
        try:
            df_y = _fetch_year(symbol, y)
            parts.append(df_y)
            print(f" {len(df_y):>7,} bars")
        except Exception as e:
            print(f" FEHLER ({e})")

    if not parts:
        raise RuntimeError(f"{symbol}: 0 bars geladen")

    df = pd.concat(parts).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df = _normalize(df)
    df.to_parquet(cache, compression="snappy")

    size_mb = cache.stat().st_size / 1e6
    print(
        f"[saved]    {symbol}: {len(df):>7,} bars "
        f"({df.index[0].date()} -> {df.index[-1].date()}) "
        f"-> {cache.name} ({size_mb:.1f} MB)"
    )
    return cache


def load_parquet(symbol: str, years: int = 15) -> pd.DataFrame:
    """API fuer data_loader.py-Integration."""
    cache = CACHE_DIR / f"{symbol}_M15_{years}y.parquet"
    if not cache.exists():
        download_symbol(symbol, years=years)
    return pd.read_parquet(cache)


# -----------------------------------------------------------------
# CLI
# -----------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="Dukascopy M15-Downloader fuer forex_bot_smc")
    p.add_argument("--force", action="store_true", help="Cache ueberschreiben")
    p.add_argument("--symbol", choices=list(SYMBOLS), help="Nur ein Symbol")
    p.add_argument("--years", type=int, default=15, help="Historien-Tiefe in Jahren (default 15)")
    args = p.parse_args()

    targets = [args.symbol] if args.symbol else list(SYMBOLS)
    t0 = datetime.now()
    for sym in targets:
        try:
            download_symbol(sym, years=args.years, force=args.force)
        except Exception as e:
            print(f"[FEHLER]   {sym}: {e}")

    dt = (datetime.now() - t0).total_seconds() / 60
    print(f"\nFertig in {dt:.1f} min. Cache: {CACHE_DIR}")


if __name__ == "__main__":
    main()