"""
live_feed.py  (forex_bot_smc - Phase 6 / M1)
============================================

Live-Datenfeed aus MetaTrader5 -> OHLCV-DataFrame in EXAKT dem Format,
das die Strategie (smc_strategy.py / smc_patterns.analyze) erwartet.

Warum dieses Modul ueberhaupt noetig ist
----------------------------------------
Der Backtest laedt M15-Bars aus Dukascopy-Parquet:
  - Spalten Title-Case: Open / High / Low / Close / Volume
  - DatetimeIndex, NAIV, per Konvention UTC
  - Index-Name "timestamp"

MT5 liefert Bars anders:
  - Felder lowercase: open/high/low/close/tick_volume + 'time' als Unix-Epoch
  - die Zeit ist in BROKER-SERVER-ZEIT (FTMO = EET = UTC+2 Winter / UTC+3 Sommer),
    NICHT in UTC.

Der Session-Filter in smc_strategy._session_ok() arbeitet mit ts.hour und
UTC-Killzones (07-10, 12-15). Wenn wir die Server-Zeit nicht auf UTC
zuruecksetzen, feuert der Filter live 2-3h verschoben -> voellig andere
Trades als im Backtest. Darum: Server-Offset erkennen und auf UTC schieben.

Output dieses Moduls ist 1:1 austauschbar mit
    data_loader.load_symbol(symbol, timeframes=["M15"])["M15"]

WICHTIG
-------
- Laeuft nur auf Windows (MetaTrader5-Paket). Auf dem Mac laesst sich das
  Modul importieren (mt5=None), aber die Funktionen werfen dann sauber.
- MT5-Terminal muss laufen + im FTMO-Konto eingeloggt sein.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

import pandas as pd

try:
    import MetaTrader5 as mt5
except ImportError:                       # Mac / kein MT5 -> Import bleibt moeglich
    mt5 = None

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
WANTED_PREFIXES = ["EURUSD", "XAUUSD", "USDJPY"]

# Default-Fenstergroesse fuer den Live-Lauf. Muss gross genug sein, damit
# Swing-/OB-/FVG-Erkennung + H4-CT/ADX (EMA50) + Vola-Median(200 H1) stabil
# sind. 5000 M15-Bars ~ 52 Handelstage. M6 (Paritaets-Check) validiert das.
DEFAULT_WINDOW_BARS = 5000

# Server->UTC-Offset in Stunden. None = automatisch erkennen.
# Nur setzen, falls die Auto-Erkennung mal danebenliegt (z.B. exotischer
# Broker mit halbstuendigem Offset).
SERVER_UTC_OFFSET_HOURS: Optional[int] = None

# Cache fuer den erkannten Offset (pro Prozess einmal erkennen reicht)
_offset_cache: Optional[int] = None


# ---------------------------------------------------------------------------
# Verbindung
# ---------------------------------------------------------------------------
def ensure_initialized() -> None:
    """Stellt sicher, dass die MT5-Verbindung steht (idempotent)."""
    if mt5 is None:
        raise RuntimeError(
            "MetaTrader5-Paket nicht verfuegbar. live_feed laeuft nur auf "
            "Windows mit installiertem MT5 + 'pip install MetaTrader5'."
        )
    if mt5.terminal_info() is None:
        if not mt5.initialize():
            raise RuntimeError(f"mt5.initialize() fehlgeschlagen: {mt5.last_error()}")


def resolve_symbol(prefix: str) -> Optional[str]:
    """Findet den echten Broker-Symbolnamen (Suffixe wie '.r', 'm', '.' etc.).
    Exakter Treffer hat Vorrang, sonst erster Praefix-Treffer.
    """
    ensure_initialized()
    if mt5.symbol_info(prefix) is not None:
        return prefix
    all_syms = mt5.symbols_get()
    if all_syms is None:
        return None
    matches = [s.name for s in all_syms if s.name.upper().startswith(prefix.upper())]
    return matches[0] if matches else None


def resolve_all() -> dict[str, str]:
    """Mappt unsere Strategie-Symbole auf die Broker-Namen."""
    out: dict[str, str] = {}
    for p in WANTED_PREFIXES:
        name = resolve_symbol(p)
        if name:
            out[p] = name
        else:
            log.warning("Symbol nicht gefunden beim Broker: %s", p)
    return out


# ---------------------------------------------------------------------------
# Server-Zeit -> UTC
# ---------------------------------------------------------------------------
def detect_server_utc_offset_hours(symbol_resolved: str) -> int:
    """Erkennt (Server-Zeit - UTC) in vollen Stunden.

    MT5-Epoch-Zeiten sind in Server-Zeitzone. Wir vergleichen die Zeit des
    letzten Ticks (Server) mit der echten UTC-Jetzt-Zeit und runden auf
    volle Stunden (alle gaengigen FX-Broker haben volle Stundenoffsets;
    FTMO = EET = +2/+3 je nach DST).
    """
    tick = mt5.symbol_info_tick(symbol_resolved)
    if tick is None or tick.time == 0:
        log.warning("Kein Tick fuer Offset-Erkennung (%s) -> Offset 0 angenommen",
                    symbol_resolved)
        return 0
    server_epoch = float(tick.time)
    utc_now = datetime.now(timezone.utc).timestamp()
    offset = (server_epoch - utc_now) / 3600.0
    return int(round(offset))


def _get_offset(symbol_resolved: str) -> int:
    global _offset_cache
    if SERVER_UTC_OFFSET_HOURS is not None:
        return SERVER_UTC_OFFSET_HOURS
    if _offset_cache is None:
        _offset_cache = detect_server_utc_offset_hours(symbol_resolved)
        log.info("Server->UTC Offset erkannt: %+d h", _offset_cache)
    return _offset_cache


# ---------------------------------------------------------------------------
# M15-Bars holen
# ---------------------------------------------------------------------------
def get_m15_bars(
    symbol_resolved: str,
    n_bars: int = DEFAULT_WINDOW_BARS,
    closed_only: bool = True,
) -> pd.DataFrame:
    """Holt die letzten n_bars M15-Bars aus MT5 als Title-Case-OHLCV-DF.

    closed_only=True (Default): die aktuell noch entstehende Bar (Pos 0)
    wird verworfen, sodass die letzte Zeile die zuletzt GESCHLOSSENE M15-Bar
    ist - genau das, worauf die Strategie beim Bar-Close prueft.

    Rueckgabe: DataFrame[Open,High,Low,Close,Volume] mit naivem UTC-Index
    (Name 'timestamp'), aufsteigend sortiert, ohne Duplikate.
    """
    ensure_initialized()
    if not mt5.symbol_select(symbol_resolved, True):
        raise RuntimeError(f"symbol_select({symbol_resolved}) fehlgeschlagen: "
                           f"{mt5.last_error()}")

    count = n_bars + 1 if closed_only else n_bars
    rates = mt5.copy_rates_from_pos(symbol_resolved, mt5.TIMEFRAME_M15, 0, count)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"copy_rates_from_pos({symbol_resolved}) leer: "
                           f"{mt5.last_error()}")

    raw = pd.DataFrame(rates)
    offset_h = _get_offset(symbol_resolved)

    # Epoch (Server-Zeit als naive Wand-Uhr) -> UTC durch Abzug des Offsets
    idx = pd.to_datetime(raw["time"], unit="s") - pd.Timedelta(hours=offset_h)

    # tick_volume als Volume (real_volume ist bei FX meist 0; Volume wird in
    # der Signal-Logik ohnehin nicht verwendet, nur durch resample-agg getragen)
    vol = raw["tick_volume"] if "tick_volume" in raw.columns else 0.0

    df = pd.DataFrame({
        "Open":   raw["open"].astype(float),
        "High":   raw["high"].astype(float),
        "Low":    raw["low"].astype(float),
        "Close":  raw["close"].astype(float),
        "Volume": pd.Series(vol, index=raw.index).astype(float),
    })
    df.index = idx
    df.index.name = "timestamp"
    df = df[~df.index.duplicated(keep="last")].sort_index()

    if closed_only:
        df = df.iloc[:-1]      # die noch entstehende Bar weglassen

    return df


# ---------------------------------------------------------------------------
# Convenience: fertige Strategie-Frames (LTF + HTF) liefern
# ---------------------------------------------------------------------------
def get_strategy_frames(
    prefix: str,
    n_bars: int = DEFAULT_WINDOW_BARS,
    htf: str = "H1",
) -> Tuple[str, pd.DataFrame, pd.DataFrame]:
    """Liefert (broker_symbol, ltf_df_M15, htf_df) - direkt verwendbar fuer
    smc_patterns.analyze() und smc_strategy.find_all_setups().

    Das HTF-Resampling nutzt dieselbe data_loader.resample()-Funktion wie der
    Backtest -> identische H1-Bars. (Den H4-Frame fuer CT/ADX resampled
    find_all_setups intern selbst, genau wie im Backtest.)
    """
    from data_loader import resample   # lokaler Import: data_loader zieht config

    name = resolve_symbol(prefix)
    if name is None:
        raise RuntimeError(f"Symbol nicht gefunden beim Broker: {prefix}")

    ltf_df = get_m15_bars(name, n_bars=n_bars, closed_only=True)
    htf_df = resample(ltf_df, htf)
    return name, ltf_df, htf_df


# ---------------------------------------------------------------------------
# Standalone-Smoketest (auf dem VPS:  python live_feed.py)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    ensure_initialized()

    print("=" * 72)
    print("  LIVE_FEED Smoketest")
    print("=" * 72)

    mapping = resolve_all()
    print("\n[SYMBOL-MAPPING]")
    for k, v in mapping.items():
        print(f"  {k} -> {v}")

    for prefix in WANTED_PREFIXES:
        if prefix not in mapping:
            print(f"\n[{prefix}] uebersprungen (nicht gefunden)")
            continue
        name, ltf, htf = get_strategy_frames(prefix, n_bars=600, htf="H1")
        off = _get_offset(name)
        print(f"\n[{prefix}]  Broker='{name}'  Server->UTC Offset {off:+d}h")
        print(f"  M15: {len(ltf)} Bars  {ltf.index[0]} -> {ltf.index[-1]} (UTC)")
        print(f"  Spalten: {list(ltf.columns)}")
        print(f"  H1 : {len(htf)} Bars  (resampled)")
        print(ltf.tail(3).to_string())

    mt5.shutdown()
    print("\nFertig.")
