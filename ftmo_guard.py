"""
ftmo_guard.py  (forex_bot_smc - Phase 6 / M4)
=============================================

Echtzeit-Wächter über die FTMO-Regeln. Verhindert, dass der Bot eine
Grenze reißt, indem er VOR dem Limit hart stoppt.

Überwachte Regeln
-----------------
1) Daily Loss  -5 %  (von der Tages-Start-Equity)
2) Total Loss -10 %  (von der initialen Account-Balance)
3) Max 3 Verluste/Tag (interne Risk-Regel aus config)

Sicherheitspuffer
-----------------
Der Guard löst bereits bei SAFETY_FRAC (80 %) des jeweiligen Limits aus:
  Daily-Trip  = 5 % * 0.8 = 4 %   (= $400 bei $10k)
  Total-Trip  = 10 % * 0.8 = 8 %  (= $800 bei $10k)
So bleibt Luft für Spread/Slippage und wir reißen die echte FTMO-Grenze nie.

Tages-Reset
-----------
Der Daily-Zähler wird zum Broker-Server-Mitternacht zurückgesetzt (FTMO
rechnet in Server-Zeit). Wir lesen die Server-Zeit direkt aus einem Tick.

Equity vs Balance
-----------------
FTMO misst inkl. offener Floating-P/L -> wir nutzen account_info().equity.

State wird in ftmo_guard_state.json persistiert (übersteht Neustart/Reconnect).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

import config
import live_feed

log = logging.getLogger("ftmo_guard")


# ---------------------------------------------------------------------------
# Limits (aus config, mit FTMO-Defaults)
# ---------------------------------------------------------------------------
INITIAL_BALANCE = float(getattr(config, "ACCOUNT_SIZE_USD", 10_000.0))
DAILY_LOSS_PCT = float(getattr(config, "FTMO_DAILY_LOSS_LIMIT", 0.05))
TOTAL_LOSS_PCT = float(getattr(config, "FTMO_MAX_LOSS_LIMIT", 0.10))
MAX_LOSSES_PER_DAY = int(getattr(config, "MAX_LOSSES_PER_DAY", 3))

SAFETY_FRAC = 0.80                                   # Trip bei 80 % des Limits

DAILY_TRIP = INITIAL_BALANCE * DAILY_LOSS_PCT * SAFETY_FRAC    # $400
TOTAL_TRIP = INITIAL_BALANCE * TOTAL_LOSS_PCT * SAFETY_FRAC    # $800

STATE_FILE = Path(__file__).with_name("ftmo_guard_state.json")


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
@dataclass
class GuardState:
    server_day: str
    day_start_equity: float
    losses_today: int = 0
    halted: bool = False
    halt_reason: str = ""


def _ensure() -> None:
    if mt5 is None:
        raise RuntimeError("MetaTrader5-Paket nur auf Windows verfuegbar.")
    live_feed.ensure_initialized()


def _server_now() -> datetime:
    """Broker-Server-Wand-Uhr (FTMO = EEST/EET) aus dem letzten Tick.
    Fallback auf UTC, falls kein Tick verfuegbar."""
    for sym in ("EURUSD", "XAUUSD", "USDJPY"):
        name = live_feed.resolve_symbol(sym)
        if name:
            t = mt5.symbol_info_tick(name)
            if t and t.time:
                return datetime.utcfromtimestamp(t.time)   # Server-Zeit als naive
    return datetime.utcnow()


def _server_day() -> str:
    return _server_now().date().isoformat()


def _equity() -> float:
    acc = mt5.account_info()
    if acc is None:
        raise RuntimeError(f"account_info() leer: {mt5.last_error()}")
    return float(acc.equity)


def _load(equity: Optional[float] = None) -> GuardState:
    if STATE_FILE.exists():
        try:
            return GuardState(**json.loads(STATE_FILE.read_text()))
        except Exception as e:                       # noqa: BLE001
            log.error("State unlesbar (%s) -> neu initialisieren", e)
    eq = equity if equity is not None else _equity()
    return GuardState(server_day=_server_day(), day_start_equity=eq)


def _save(st: GuardState) -> None:
    STATE_FILE.write_text(json.dumps(asdict(st), indent=2))


# ---------------------------------------------------------------------------
# Kern-Logik
# ---------------------------------------------------------------------------
def check(equity: Optional[float] = None) -> Tuple[bool, str]:
    """Prüft die Equity gegen Daily-/Total-Trip. Setzt bei Verletzung halted.
    Rückgabe: (ok, reason)."""
    _ensure()
    eq = equity if equity is not None else _equity()
    st = _load(eq)

    # Tages-Rollover (Server-Zeit) -> Daily reset
    today = _server_day()
    if st.server_day != today:
        st.server_day = today
        st.day_start_equity = eq
        st.losses_today = 0
        if st.halted and st.halt_reason.startswith("DAILY"):
            st.halted = False
            st.halt_reason = ""
            log.info("Neuer Handelstag -> Daily-Halt aufgehoben")
        _save(st)

    if st.halted:
        return False, st.halt_reason

    daily_loss = st.day_start_equity - eq
    total_loss = INITIAL_BALANCE - eq

    if daily_loss >= DAILY_TRIP:
        st.halted = True
        st.halt_reason = (f"DAILY-TRIP: Tagesverlust ${daily_loss:.2f} "
                          f">= ${DAILY_TRIP:.2f} (80% von 5%)")
        _save(st)
        return False, st.halt_reason

    if total_loss >= TOTAL_TRIP:
        st.halted = True
        st.halt_reason = (f"TOTAL-TRIP: Gesamtverlust ${total_loss:.2f} "
                          f">= ${TOTAL_TRIP:.2f} (80% von 10%)")
        _save(st)
        return False, st.halt_reason

    return True, "ok"


def can_open() -> Tuple[bool, str]:
    """Darf ein neuer Trade geöffnet werden? (Daily/Total + 3-Verluste-Regel)."""
    ok, reason = check()
    if not ok:
        return False, reason
    st = _load()
    if st.losses_today >= MAX_LOSSES_PER_DAY:
        return False, f"{st.losses_today} Verluste heute (max {MAX_LOSSES_PER_DAY})"
    return True, "ok"


def enforce() -> bool:
    """Kill-Switch: bei Limit-Verletzung ALLE Positionen schließen.
    Rückgabe: True wenn ge-haltet (= Limit verletzt)."""
    ok, reason = check()
    if ok:
        return False
    log.critical("FTMO-GUARD HALT: %s -> alle Positionen schliessen!", reason)
    try:
        import executor
        executor.close_all()
    except Exception as e:                           # noqa: BLE001
        log.error("Flatten beim Halt fehlgeschlagen: %s", e)
    return True


def register_loss() -> None:
    """Vom Executor aufzurufen, wenn ein Trade mit Verlust geschlossen wurde."""
    st = _load()
    st.losses_today += 1
    _save(st)
    log.info("Verlust registriert -> %d/%d heute", st.losses_today, MAX_LOSSES_PER_DAY)


def reset(clear_total: bool = True) -> None:
    """Manueller Reset (Demo / neue Challenge): Halt + Tageszähler löschen."""
    eq = _equity()
    st = GuardState(server_day=_server_day(), day_start_equity=eq)
    _save(st)
    log.info("Guard zurückgesetzt. day_start_equity=$%.2f", eq)


def snapshot() -> dict:
    _ensure()
    eq = _equity()
    st = _load(eq)
    daily_loss = st.day_start_equity - eq
    total_loss = INITIAL_BALANCE - eq
    return {
        "equity": eq,
        "initial_balance": INITIAL_BALANCE,
        "server_day": st.server_day,
        "day_start_equity": st.day_start_equity,
        "daily_loss": daily_loss,
        "daily_trip": DAILY_TRIP,
        "daily_hard": INITIAL_BALANCE * DAILY_LOSS_PCT,
        "total_loss": total_loss,
        "total_trip": TOTAL_TRIP,
        "total_hard": INITIAL_BALANCE * TOTAL_LOSS_PCT,
        "losses_today": st.losses_today,
        "max_losses": MAX_LOSSES_PER_DAY,
        "halted": st.halted,
        "halt_reason": st.halt_reason,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    ap = argparse.ArgumentParser(description="FTMO-Guard (Phase 6 / M4)")
    ap.add_argument("--status", action="store_true", help="Aktuellen Guard-Status zeigen")
    ap.add_argument("--reset", action="store_true", help="Halt + Tageszähler zurücksetzen")
    ap.add_argument("--check", action="store_true", help="Einen Check ausführen (ok/halt)")
    args = ap.parse_args()

    _ensure()
    if args.reset:
        reset()
        print("Guard zurückgesetzt.")
    elif args.check:
        ok, reason = check()
        print(f"can-trade: {ok}  | {reason}")
    else:
        s = snapshot()
        print("=" * 64)
        print("  FTMO GUARD STATUS")
        print("=" * 64)
        print(f"  Initial Balance : ${s['initial_balance']:.2f}")
        print(f"  Aktuelle Equity : ${s['equity']:.2f}")
        print(f"  Server-Tag      : {s['server_day']}")
        print(f"  Tages-Start-Eq. : ${s['day_start_equity']:.2f}")
        print(f"  Tagesverlust    : ${s['daily_loss']:.2f}  "
              f"(Trip ${s['daily_trip']:.2f} / hart ${s['daily_hard']:.2f})")
        print(f"  Gesamtverlust   : ${s['total_loss']:.2f}  "
              f"(Trip ${s['total_trip']:.2f} / hart ${s['total_hard']:.2f})")
        print(f"  Verluste heute  : {s['losses_today']} / {s['max_losses']}")
        print(f"  HALTED          : {s['halted']}  {s['halt_reason']}")
        print("=" * 64)

    mt5.shutdown()
