"""
live_runner.py  (forex_bot_smc - Phase 6 / M2)
==============================================

Live-Loop: wartet auf jeden M15-Bar-Close und wendet die EXAKT gleiche
Setup-Logik wie der Backtest an (smc_patterns.analyze + smc_strategy.
find_all_setups). Erkennt ein Setup auf der zuletzt geschlossenen Bar,
loggt es (Dry-Run) oder reicht es an den Executor weiter (M3).

Parität zum Backtest
--------------------
1) Identischer Strategie-Code (find_all_setups) auf einem rollierenden
   M15-Fenster aus live_feed.get_strategy_frames().
2) PER-SYMBOL-SETTINGS wie im Backtest-CLI (backtest_m15.py __main__):
     EURUSD -> direction "auto"  (Config-Default = shorts-only)
     XAUUSD -> direction "both"
     USDJPY -> direction "both"  + CT/ADX-Filter AUS (Exclude-Liste)
   Diese werden pro Symbol um den find_all_setups-Aufruf gesetzt und
   danach wieder zurueckgesetzt (der Bot fuehrt alle 3 Symbole in EINEM
   Prozess - ohne Reset wuerde z.B. USDJPY mit CT/ADX laufen).
3) Nur Setups auf der zuletzt GESCHLOSSENEN Bar zaehlen (live_feed liefert
   closed_only=True). Entry = Bar-Close, genau wie im Backtest.

Sicherheit
----------
- Default ist DRY-RUN: es werden KEINE Orders geschickt, nur geloggt.
- Echte Orders erst mit --live (und erst wenn M3/executor.py verdrahtet ist).

Aufruf (auf dem VPS)
--------------------
    python live_runner.py --once            # ein Detektionszyklus jetzt
    python live_runner.py                    # Dauerschleife, Dry-Run
    python live_runner.py --live             # Dauerschleife, ECHTE Orders (spaeter)
"""
from __future__ import annotations

import argparse
import contextlib
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Tuple

import config
from smc_patterns import analyze
from smc_strategy import find_all_setups, Setup

import live_feed

log = logging.getLogger("live_runner")


# ---------------------------------------------------------------------------
# Per-Symbol-Run-Config (spiegelt backtest_m15.py CLI-Aufrufe der v0.6-Validierung)
# ---------------------------------------------------------------------------
SYMBOL_DIRECTION = {
    "EURUSD": "auto",     # Config-Default: TRADE_LONGS=False, TRADE_SHORTS=True
    "XAUUSD": "both",
    "USDJPY": "both",
}


@contextlib.contextmanager
def symbol_config(symbol: str):
    """Setzt TRADE_LONGS/TRADE_SHORTS + CT_ADX_FILTER_ENABLED passend zum
    Symbol (wie der Backtest-CLI) und stellt danach den Originalzustand wieder
    her. smc_strategy liest diese config-Werte zur Laufzeit.
    """
    saved = (
        getattr(config, "TRADE_LONGS", True),
        getattr(config, "TRADE_SHORTS", True),
        getattr(config, "CT_ADX_FILTER_ENABLED", False),
    )
    try:
        direction = SYMBOL_DIRECTION.get(symbol, "auto")
        if direction == "both":
            config.TRADE_LONGS = True
            config.TRADE_SHORTS = True
        elif direction == "shorts-only":
            config.TRADE_LONGS = False
            config.TRADE_SHORTS = True
        elif direction == "longs-only":
            config.TRADE_LONGS = True
            config.TRADE_SHORTS = False
        # "auto" -> Config-Defaults unveraendert lassen

        # CT/ADX Per-Symbol-Exclude (wie backtest_m15.py __main__)
        exclude = getattr(config, "CT_ADX_FILTER_EXCLUDE_SYMBOLS", [])
        if symbol in exclude and getattr(config, "CT_ADX_FILTER_ENABLED", False):
            config.CT_ADX_FILTER_ENABLED = False

        yield
    finally:
        config.TRADE_LONGS, config.TRADE_SHORTS, config.CT_ADX_FILTER_ENABLED = saved


# ---------------------------------------------------------------------------
# Scheduling: bis zum naechsten M15-Close warten
# ---------------------------------------------------------------------------
def seconds_until_next_m15(buffer_s: int = 10) -> float:
    """Sekunden bis zum naechsten M15-Boundary (xx:00/15/30/45) + Puffer,
    damit MT5 die geschlossene Bar finalisiert hat. Alles in UTC."""
    now = datetime.now(timezone.utc)
    add_min = 15 - (now.minute % 15)
    nxt = now.replace(second=0, microsecond=0) + timedelta(minutes=add_min)
    target = nxt + timedelta(seconds=buffer_s)
    return max(1.0, (target - now).total_seconds())


# ---------------------------------------------------------------------------
# Detektion pro Symbol
# ---------------------------------------------------------------------------
def detect_setups_for_symbol(
    prefix: str,
    n_bars: int,
) -> Tuple[str, "datetime", List[Setup]]:
    """Liefert (broker_symbol, last_bar_ts, setups_auf_letzter_bar).

    `prefix` (z.B. "EURUSD") geht an die Strategie (fuer Per-Symbol-
    Dead-Zones + Direction/CT-ADX-Config). Der Broker-Name (mit evtl.
    Suffix) wird fuer den Executor zurueckgegeben.
    """
    name, ltf, htf = live_feed.get_strategy_frames(prefix, n_bars=n_bars)

    htf_snap = analyze(htf)
    ltf_snap = analyze(ltf)

    with symbol_config(prefix):
        setups = find_all_setups(ltf, ltf_snap, htf_snap, htf_df=htf, symbol=prefix)

    last_ts = ltf.index[-1]
    fresh = [s for s in setups if s.entry_time == last_ts]
    return name, last_ts, fresh


# ---------------------------------------------------------------------------
# Signal-Handling
# ---------------------------------------------------------------------------
def _log_signal(prefix: str, broker: str, s: Setup, dry_run: bool) -> None:
    tag = "DRY-RUN" if dry_run else "LIVE"
    rr_risk = abs(s.entry_price - s.sl)
    log.info(
        "[%s SIGNAL] %s (%s)  %s  E=%.5f  SL=%.5f  TP1=%.5f  TP2=%.5f  "
        "risk=%.5f  zone=%s  bias=%s  @%s",
        tag, prefix, broker, s.direction.upper(),
        s.entry_price, s.sl, s.tp1, s.tp2, rr_risk,
        s.zone_kind, s.htf_bias, s.entry_time,
    )


def run_cycle(
    prefixes: List[str],
    n_bars: int,
    dry_run: bool,
    last_signaled: dict,
    on_signal: Optional[Callable[[str, str, Setup], None]] = None,
) -> int:
    """Ein Detektionszyklus ueber alle Symbole. Gibt Anzahl neuer Signale zurueck."""
    n_new = 0
    for prefix in prefixes:
        try:
            broker, last_ts, fresh = detect_setups_for_symbol(prefix, n_bars)
        except Exception as e:                       # noqa: BLE001
            log.error("Detection-Fehler %s: %s", prefix, e)
            continue

        if not fresh:
            log.debug("%s: kein Setup auf %s", prefix, last_ts)
            continue

        # Dedup: dieselbe Bar nicht doppelt signalisieren
        if last_signaled.get(prefix) == last_ts:
            continue
        last_signaled[prefix] = last_ts

        for s in fresh:
            _log_signal(prefix, broker, s, dry_run)
            n_new += 1
            if on_signal is not None and not dry_run:
                try:
                    on_signal(prefix, broker, s)        # -> M3 executor.open_from_setup
                except Exception as e:                  # noqa: BLE001
                    log.error("on_signal-Fehler %s: %s", prefix, e)
    return n_new


# ---------------------------------------------------------------------------
# Hauptschleife
# ---------------------------------------------------------------------------
def run_loop(
    prefixes: List[str],
    n_bars: int,
    dry_run: bool,
    buffer_s: int = 10,
    on_signal: Optional[Callable[[str, str, Setup], None]] = None,
) -> None:
    last_signaled: dict = {}
    log.info("Live-Loop gestartet | Symbole=%s | Fenster=%d Bars | %s",
             prefixes, n_bars, "DRY-RUN" if dry_run else "LIVE-ORDERS")
    try:
        while True:
            wait = seconds_until_next_m15(buffer_s)
            log.info("Warte %.0fs bis zum naechsten M15-Close ...", wait)
            time.sleep(wait)
            t0 = time.time()
            n = run_cycle(prefixes, n_bars, dry_run, last_signaled, on_signal)
            log.info("Zyklus fertig in %.1fs | neue Signale: %d", time.time() - t0, n)
    except KeyboardInterrupt:
        log.info("Abbruch per Ctrl-C - beende sauber.")
    finally:
        if live_feed.mt5 is not None:
            live_feed.mt5.shutdown()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="SMC M15 Live-Runner (Phase 6)")
    ap.add_argument("--symbols", default="EURUSD,XAUUSD,USDJPY",
                    help="Komma-getrennt. Default: EURUSD,XAUUSD,USDJPY")
    ap.add_argument("--window", type=int, default=live_feed.DEFAULT_WINDOW_BARS,
                    help=f"M15-Fenstergroesse (Default {live_feed.DEFAULT_WINDOW_BARS}).")
    ap.add_argument("--once", action="store_true",
                    help="Nur EIN Detektionszyklus jetzt (zum Testen).")
    ap.add_argument("--live", action="store_true",
                    help="ECHTE Orders schicken (Default: Dry-Run, nur loggen). "
                         "Wirkt erst wenn M3/executor.py verdrahtet ist.")
    ap.add_argument("--buffer", type=int, default=10,
                    help="Sekunden Puffer nach M15-Close (Default 10).")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    prefixes = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    dry_run = not args.live

    on_signal = None
    if args.live:
        # M3: hier wird spaeter der Executor eingehaengt:
        #   from executor import open_from_setup as on_signal
        log.warning("--live gesetzt, aber executor.py (M3) ist noch nicht "
                    "verdrahtet -> es werden KEINE Orders geschickt.")
        dry_run = True

    if args.once:
        last_signaled: dict = {}
        n = run_cycle(prefixes, args.window, dry_run, last_signaled, on_signal)
        log.info("Einmaliger Zyklus fertig | neue Signale: %d", n)
        if live_feed.mt5 is not None:
            live_feed.mt5.shutdown()
        return 0

    run_loop(prefixes, args.window, dry_run, buffer_s=args.buffer, on_signal=on_signal)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
