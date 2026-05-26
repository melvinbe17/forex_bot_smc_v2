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
- Mit --live: Executor (M3) + FTMO-Guard (M4) aktiv -> echte Orders, jeder
  Zyklus managed offene Positionen und prueft die FTMO-Limits (Kill-Switch).

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
import executor
import ftmo_guard
import heartbeat

log = logging.getLogger("live_runner")


def _write_heartbeat(n_new: int, live: bool) -> None:
    """Schreibt Status + Lebenszeichen in heartbeat.json. Laeuft im Runner-
    Prozess (der die MT5-Verbindung hat); daily_summary/watchdog lesen nur
    die Datei, ohne eigene MT5-Verbindung. Fehler werden geloggt, nie geworfen."""
    try:
        acc = live_feed.mt5.account_info()
        snap = ftmo_guard.snapshot()
        allp = live_feed.mt5.positions_get() or []
        ours = [p for p in allp if p.magic == executor.MAGIC]
        positions = [{
            "symbol": p.symbol,
            "side": "long" if p.type == live_feed.mt5.POSITION_TYPE_BUY else "short",
            "vol": p.volume, "sl": p.sl, "profit": round(p.profit, 2),
        } for p in ours]
        realized = None
        try:
            now = ftmo_guard._server_now().replace(tzinfo=None)
            ds = now.replace(hour=0, minute=0, second=0, microsecond=0)
            deals = live_feed.mt5.history_deals_get(ds, now + timedelta(hours=1)) or []
            closed = [d for d in deals
                      if d.magic == executor.MAGIC
                      and d.entry == live_feed.mt5.DEAL_ENTRY_OUT]
            realized = {"count": len(closed),
                        "profit": round(sum(d.profit for d in closed), 2)}
        except Exception:                            # noqa: BLE001
            pass
        heartbeat.write({
            "mode": "live" if live else "dry",
            "balance": round(acc.balance, 2) if acc else None,
            "equity": round(snap["equity"], 2),
            "server_day": snap["server_day"],
            "daily_loss": round(snap["daily_loss"], 2),
            "daily_trip": snap["daily_trip"],
            "total_loss": round(snap["total_loss"], 2),
            "total_trip": snap["total_trip"],
            "losses_today": snap["losses_today"],
            "max_losses": snap["max_losses"],
            "halted": snap["halted"],
            "halt_reason": snap["halt_reason"],
            "open_positions": positions,
            "last_signals": n_new,
            "realized_today": realized,
        })
    except Exception as e:                           # noqa: BLE001
        log.error("Heartbeat schreiben fehlgeschlagen: %s", e)


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
    live: bool,
    last_signaled: dict,
) -> int:
    """Ein Zyklus. dry (live=False): nur Signale loggen. live=True: zusaetzlich
    offene Positionen managen, FTMO-Guard durchsetzen und Entries ausfuehren.
    Gibt Anzahl neuer Signale zurueck."""

    halted = False
    if live:
        # 1) Bestehende Positionen managen (TP1/TP2/BE/Max-Hold)
        try:
            executor.manage_open_positions()
        except Exception as e:                       # noqa: BLE001
            log.error("manage_open_positions-Fehler: %s", e)
        # 2) Kill-Switch: bei Limit-Verletzung flatten + keine neuen Entries
        try:
            halted = ftmo_guard.enforce()
        except Exception as e:                       # noqa: BLE001
            log.error("ftmo_guard.enforce-Fehler: %s", e)
        if halted:
            log.warning("FTMO-Guard HALT aktiv -> keine neuen Entries diesen Zyklus")

    # 3) Signale erkennen (+ ggf. ausfuehren)
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
            _log_signal(prefix, broker, s, dry_run=not live)
            n_new += 1
            if not live or halted:
                continue
            # FTMO-Guard-Gate vor jedem Entry
            ok, reason = ftmo_guard.can_open()
            if not ok:
                log.warning("Entry %s blockiert vom Guard: %s", prefix, reason)
                continue
            if executor.has_open_position(prefix):
                log.info("%s: bereits offene Position -> kein Entry", prefix)
                continue
            try:
                executor.open_from_setup(prefix, broker, s, dry=False)
            except Exception as e:                   # noqa: BLE001
                log.error("Entry %s fehlgeschlagen: %s", prefix, e)

    _write_heartbeat(n_new, live)
    return n_new


# ---------------------------------------------------------------------------
# Hauptschleife
# ---------------------------------------------------------------------------
def run_loop(
    prefixes: List[str],
    n_bars: int,
    live: bool,
    buffer_s: int = 10,
) -> None:
    last_signaled: dict = {}
    log.info("Live-Loop gestartet | Symbole=%s | Fenster=%d Bars | Modus=%s",
             prefixes, n_bars, "LIVE-ORDERS" if live else "DRY-RUN")
    _write_heartbeat(0, live)        # initiales Lebenszeichen direkt beim Start
    try:
        while True:
            wait = seconds_until_next_m15(buffer_s)
            log.info("Warte %.0fs bis zum naechsten M15-Close ...", wait)
            time.sleep(wait)
            t0 = time.time()
            n = run_cycle(prefixes, n_bars, live, last_signaled)
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
                         "Aktiviert Executor + FTMO-Guard + Positions-Management.")
    ap.add_argument("--buffer", type=int, default=10,
                    help="Sekunden Puffer nach M15-Close (Default 10).")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    prefixes = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    live = args.live

    if live:
        log.warning("LIVE-MODUS: es werden ECHTE Orders auf dem Konto geschickt "
                    "(Executor + FTMO-Guard aktiv).")

    if args.once:
        last_signaled: dict = {}
        n = run_cycle(prefixes, args.window, live, last_signaled)
        log.info("Einmaliger Zyklus fertig | neue Signale: %d", n)
        if live_feed.mt5 is not None:
            live_feed.mt5.shutdown()
        return 0

    run_loop(prefixes, args.window, live, buffer_s=args.buffer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
