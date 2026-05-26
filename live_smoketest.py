"""
live_smoketest.py  (forex_bot_smc - Phase 6 / M0)
=================================================

Verbindungs-Smoketest fuer die Live-Umgebung (Windows + MetaTrader5).

Zweck
-----
Prueft VOR dem Bau der Live-Module, ob:
  1) das MetaTrader5-Python-Paket den laufenden MT5-Terminal erreicht,
  2) das FTMO-Konto eingeloggt ist und die Balance lesbar ist,
  3) unsere drei Symbole (EURUSD / XAUUSD / USDJPY) verfuegbar sind
     und wie sie beim Broker exakt heissen (Suffixe!),
  4) Live-M15-Bars in genau dem OHLCV-Format ankommen, das die
     Strategie erwartet (Open/High/Low/Close - Title-Case).

WICHTIG
-------
- Laeuft NUR auf Windows (das MetaTrader5-Paket ist Windows-only).
- Der MT5-Terminal muss LAUFEN und im FTMO-Konto eingeloggt sein.
- In MT5: "Algo Trading"-Button aktiv + Tools > Optionen > Expert Advisors
  > "Allow algorithmic trading" angehakt.
- Python-Version: nimm x64 Python 3.11 oder 3.12 (das MetaTrader5-Wheel
  hinkt der neuesten Python-Version hinterher - 3.14 vom Mac NICHT 1:1
  uebernehmen).

Aufruf
------
    python live_smoketest.py
"""
from __future__ import annotations

import sys

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[FEHLER] MetaTrader5-Paket fehlt. Auf dem Windows-VPS:")
    print("         pip install MetaTrader5 pandas numpy")
    sys.exit(1)

import pandas as pd


# Unsere drei Strategie-Symbole. Broker haengen oft Suffixe an
# (z.B. 'EURUSD.r', 'XAUUSD.', 'USDJPYm'), darum suchen wir per Praefix.
WANTED = ["EURUSD", "XAUUSD", "USDJPY"]
N_BARS = 5


def resolve_symbol(prefix: str) -> str | None:
    """Findet den echten Broker-Symbolnamen, der mit `prefix` beginnt.
    Gibt den ersten Treffer zurueck oder None.
    """
    # Exakter Treffer zuerst
    if mt5.symbol_info(prefix) is not None:
        return prefix
    # Sonst per Praefix-Suche ueber alle Symbole
    all_syms = mt5.symbols_get()
    if all_syms is None:
        return None
    matches = [s.name for s in all_syms if s.name.upper().startswith(prefix.upper())]
    return matches[0] if matches else None


def main() -> int:
    print("=" * 70)
    print("  LIVE SMOKETEST  -  MT5 / FTMO Verbindung")
    print("=" * 70)

    # 1) Verbindung zum Terminal
    if not mt5.initialize():
        print(f"[FEHLER] mt5.initialize() fehlgeschlagen: {mt5.last_error()}")
        print("        -> Laeuft der MT5-Terminal? Ist er eingeloggt?")
        return 1

    term = mt5.terminal_info()
    print("\n[TERMINAL]")
    print(f"  Name        : {getattr(term, 'name', '?')}")
    print(f"  Build       : {getattr(term, 'build', '?')}")
    print(f"  Connected   : {getattr(term, 'connected', '?')}")
    print(f"  Algo erlaubt: {getattr(term, 'trade_allowed', '?')}")

    # 2) Konto-Info
    acc = mt5.account_info()
    if acc is None:
        print(f"[FEHLER] account_info() leer: {mt5.last_error()}")
        mt5.shutdown()
        return 1
    print("\n[KONTO]")
    print(f"  Login       : {acc.login}")
    print(f"  Server      : {acc.server}")
    print(f"  Firma       : {acc.company}")
    print(f"  Balance     : {acc.balance:.2f} {acc.currency}")
    print(f"  Equity      : {acc.equity:.2f} {acc.currency}")
    print(f"  Leverage    : 1:{acc.leverage}")
    print(f"  Trade-Modus : {acc.trade_mode}  (0=demo, 1=contest, 2=real)")

    # 3) + 4) Symbole aufloesen + Live-Bars ziehen
    print("\n[SYMBOLE & LIVE-BARS]")
    resolved: dict[str, str] = {}
    for want in WANTED:
        name = resolve_symbol(want)
        if name is None:
            print(f"  {want:7} -> NICHT GEFUNDEN beim Broker!")
            continue
        resolved[want] = name

        # Symbol im Market Watch aktivieren (sonst keine Bars/Ticks)
        if not mt5.symbol_select(name, True):
            print(f"  {want:7} -> '{name}' konnte nicht selektiert werden")
            continue

        tick = mt5.symbol_info_tick(name)
        bid = getattr(tick, "bid", None) if tick else None

        rates = mt5.copy_rates_from_pos(name, mt5.TIMEFRAME_M15, 0, N_BARS)
        if rates is None or len(rates) == 0:
            print(f"  {want:7} -> '{name}'  bid={bid}  KEINE M15-Bars: {mt5.last_error()}")
            continue

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        last = df.iloc[-1]
        suffix_note = "" if name == want else f"  (Broker-Name: '{name}')"
        print(f"  {want:7} -> bid={bid}  letzte M15 {last['time']}  "
              f"O={last['open']} H={last['high']} L={last['low']} C={last['close']}{suffix_note}")

    # Zusammenfassung Symbol-Mapping (brauchen wir spaeter in der Config)
    print("\n[SYMBOL-MAPPING fuer config]")
    if resolved:
        for want, name in resolved.items():
            print(f"  {want} -> {name}")
    else:
        print("  KEINE Symbole aufgeloest - Broker-Symbolnamen pruefen!")

    print("\n" + "=" * 70)
    ok = len(resolved) == len(WANTED) and acc.balance > 0
    print("  ERGEBNIS:", "OK - Verbindung steht, alle Symbole da." if ok
          else "TEILWEISE - siehe Warnungen oben.")
    print("=" * 70)

    mt5.shutdown()
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
