"""
executor.py  (forex_bot_smc - Phase 6 / M3)
===========================================

Wandelt ein Setup-Signal (aus smc_strategy / live_runner) in eine echte
MT5-Order um und managed die offene Position 1:1 wie der Backtest
(backtest_m15.simulate):

  - Entry  = Market-Order beim Bar-Close (Setup.entry_price ~ Close)
  - SL     = Setup.sl  (echter Broker-Stop)
  - TP1    = Setup.tp1 (2R)  -> 50 % schliessen, dann SL auf Break-Even
  - TP2    = Setup.tp2 (4R)  -> 25 % schliessen
  - Runner = restliche 25 %  -> laeuft bis SL/BE oder Max-Hold
  - Max-Hold = 48 M15-Bars (12h) -> Rest bei Market schliessen
  - 1 Position pro Symbol (kein Pyramiding), bis zu 3 portfolioweit

Sicherheit
----------
- LIVE_RISK_PCT = 0.5 % (Go/No-Go-Auflage fuer die ersten 3 Monate),
  bewusst niedriger als config.RISK_PER_TRADE (0.75 %).
- open_from_setup(..., dry=True) rechnet NUR die Lot-Groesse und schickt
  KEINE Order. Erst dry=False ordert wirklich.
- Standalone-Aufruf `python executor.py` macht NUR den Lot-Sizing-Dry-Check
  fuer alle 3 Symbole - kein Trade.

Position-State wird in live_positions.json persistiert, damit ein Neustart
(z.B. nach RDP-Reconnect) die offenen Trades weiter managen kann.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

import live_feed
import notify

log = logging.getLogger("executor")


# ---------------------------------------------------------------------------
# Parameter
# ---------------------------------------------------------------------------
MAGIC = 6_020_260                 # eindeutige ID unserer Trades im Terminal
LIVE_RISK_PCT = 0.005             # 0.5 % pro Trade (Go/No-Go-Auflage)
TP1_FRAC = 0.50
TP2_FRAC = 0.25
MAX_HOLD_BARS = 48                # 48 * M15 = 12h
DEVIATION = 20                    # max. Slippage in Points fuer Market-Order
POSITIONS_FILE = Path(__file__).with_name("live_positions.json")

# Beispiel-SL-Distanzen nur fuer den Dry-Check (kein Trade), grobe
# realistische Stops je Symbol, um die Lot-Mathematik zu zeigen.
_SAMPLE_SL_DIST = {"EURUSD": 0.0020, "USDJPY": 0.20, "XAUUSD": 5.0}


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------
def _ensure() -> None:
    if mt5 is None:
        raise RuntimeError("MetaTrader5-Paket nur auf Windows verfuegbar.")
    live_feed.ensure_initialized()


def _step_decimals(step: float) -> int:
    s = f"{step:.10f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


def _filling_mode(info) -> int:
    """Waehlt den vom Symbol erlaubten Filling-Modus (FOK > IOC > RETURN).

    Das MetaTrader5-Paket exportiert die SYMBOL_FILLING_*-Flags NICHT als
    Konstanten, darum die Rohwerte der Bitmaske in symbol_info.filling_mode:
      SYMBOL_FILLING_FOK = 1, SYMBOL_FILLING_IOC = 2
    Rueckgabe sind die ORDER_FILLING_*-Konstanten (die existieren).
    """
    fm = getattr(info, "filling_mode", 0)
    if fm & 1:        # FOK erlaubt
        return mt5.ORDER_FILLING_FOK
    if fm & 2:        # IOC erlaubt
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


# ---------------------------------------------------------------------------
# Lot-Sizing
# ---------------------------------------------------------------------------
def compute_lots(broker: str, entry_price: float, sl_price: float,
                 risk_amount: float) -> tuple[float, dict]:
    """Berechnet die Lot-Groesse so, dass ein SL-Treffer ~risk_amount kostet.

    Rueckgabe: (lots, diagnostics). lots ist auf volume_step abgerundet und
    auf [volume_min, volume_max] geklemmt.
    """
    _ensure()
    info = mt5.symbol_info(broker)
    if info is None:
        raise RuntimeError(f"symbol_info({broker}) leer: {mt5.last_error()}")

    tick_size = info.trade_tick_size or info.point
    tick_value = info.trade_tick_value
    sl_dist = abs(entry_price - sl_price)
    if sl_dist <= 0 or tick_size <= 0 or tick_value <= 0:
        raise ValueError(f"Ungueltige Sizing-Inputs: sl_dist={sl_dist}, "
                         f"tick_size={tick_size}, tick_value={tick_value}")

    loss_per_lot = (sl_dist / tick_size) * tick_value      # $ Verlust bei 1.0 Lot
    raw_lots = risk_amount / loss_per_lot

    step = info.volume_step or 0.01
    lots = math.floor(raw_lots / step) * step
    lots = round(lots, _step_decimals(step))

    warn = None
    if lots < info.volume_min:
        # Min-Lot riskiert mehr als das Ziel -> auf Min anheben + warnen
        lots = info.volume_min
        warn = "lots < volume_min -> auf volume_min angehoben (Risk > Ziel)"
    if lots > info.volume_max:
        lots = info.volume_max
        warn = "lots > volume_max -> gekappt"

    actual_risk = lots * loss_per_lot
    diag = {
        "tick_size": tick_size, "tick_value": tick_value,
        "sl_dist": sl_dist, "loss_per_lot": loss_per_lot,
        "raw_lots": raw_lots, "lots": lots,
        "volume_min": info.volume_min, "volume_step": step,
        "actual_risk": actual_risk, "warn": warn,
    }
    return lots, diag


# ---------------------------------------------------------------------------
# Position-State Persistenz
# ---------------------------------------------------------------------------
@dataclass
class LivePosition:
    prefix: str
    broker: str
    ticket: int
    direction: str            # "long" | "short"
    entry_price: float
    initial_sl: float
    tp1: float
    tp2: float
    lots_initial: float
    entry_time_utc: str       # ISO
    tp1_done: bool = False
    tp2_done: bool = False
    be_moved: bool = False


def _load_positions() -> dict[str, LivePosition]:
    if not POSITIONS_FILE.exists():
        return {}
    try:
        raw = json.loads(POSITIONS_FILE.read_text())
        return {k: LivePosition(**v) for k, v in raw.items()}
    except Exception as e:                       # noqa: BLE001
        log.error("Konnte %s nicht lesen: %s", POSITIONS_FILE.name, e)
        return {}


def _save_positions(positions: dict[str, LivePosition]) -> None:
    data = {k: asdict(v) for k, v in positions.items()}
    POSITIONS_FILE.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Order oeffnen
# ---------------------------------------------------------------------------
def has_open_position(prefix: str) -> bool:
    """True wenn wir fuer dieses Symbol schon eine (von uns geoeffnete) Position halten."""
    positions = _load_positions()
    return prefix in positions


def open_from_setup(prefix: str, broker: str, setup, dry: bool = True
                    ) -> Optional[LivePosition]:
    """Oeffnet eine Market-Position aus einem Setup. dry=True -> nur Lot-Rechnung."""
    _ensure()

    # 1 Position pro Symbol (kein Pyramiding, wie Backtest)
    if not dry and has_open_position(prefix):
        log.info("%s: bereits offene Position -> kein neuer Entry", prefix)
        return None

    acc = mt5.account_info()
    if acc is None:
        raise RuntimeError(f"account_info() leer: {mt5.last_error()}")
    risk_amount = acc.balance * LIVE_RISK_PCT

    lots, diag = compute_lots(broker, setup.entry_price, setup.sl, risk_amount)

    log.info("[LOT] %s %s  entry=%.5f sl=%.5f  risk=$%.2f (%.2f%%)  "
             "-> lots=%s  (actual risk $%.2f)%s",
             prefix, setup.direction, setup.entry_price, setup.sl,
             risk_amount, LIVE_RISK_PCT * 100, lots, diag["actual_risk"],
             f"  WARN: {diag['warn']}" if diag["warn"] else "")

    if dry:
        return None

    info = mt5.symbol_info(broker)
    tick = mt5.symbol_info_tick(broker)
    if setup.direction == "long":
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask
    else:
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": broker,
        "volume": float(lots),
        "type": order_type,
        "price": float(price),
        "sl": float(setup.sl),
        "tp": 0.0,                       # TPs managen wir manuell (Teilschliessungen)
        "deviation": DEVIATION,
        "magic": MAGIC,
        "comment": f"smc {prefix} {setup.zone_kind}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _filling_mode(info),
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        log.error("ORDER FEHLGESCHLAGEN %s: retcode=%s comment=%s",
                  prefix, getattr(result, "retcode", "?"),
                  getattr(result, "comment", mt5.last_error()))
        return None

    # Echten Fuellkurs + Positions-Ticket aus der offenen Position holen.
    # result.price ist bei manchen Brokern 0 -> price_open der Position ist
    # verlaesslich (wichtig fuer das spaetere Break-Even-Verschieben).
    ticket = int(result.order)
    fill_price = float(getattr(result, "price", 0.0) or 0.0)
    mine = [p for p in (mt5.positions_get(symbol=broker) or []) if p.magic == MAGIC]
    if mine:
        bp = mine[-1]
        ticket = int(bp.ticket)
        fill_price = float(bp.price_open)
    if not fill_price:
        fill_price = float(setup.entry_price)        # Fallback

    pos = LivePosition(
        prefix=prefix, broker=broker, ticket=ticket,
        direction=setup.direction, entry_price=fill_price,
        initial_sl=float(setup.sl), tp1=float(setup.tp1), tp2=float(setup.tp2),
        lots_initial=float(lots),
        entry_time_utc=datetime.now(timezone.utc).isoformat(),
    )
    positions = _load_positions()
    positions[prefix] = pos
    _save_positions(positions)
    log.info("[OPEN] %s %s  ticket=%s  lots=%s  @%.5f  SL=%.5f",
             prefix, setup.direction, pos.ticket, lots, pos.entry_price, pos.initial_sl)
    notify.trade_opened(prefix, setup.direction, float(lots), pos.entry_price,
                        pos.initial_sl, pos.tp1, pos.tp2, pos.ticket)
    return pos


# ---------------------------------------------------------------------------
# Teilschliessung / SL-Modify / Management
# ---------------------------------------------------------------------------
def _send_close(broker: str, ticket: int, direction: str,
                volume: float, reason: str) -> bool:
    """Schliesst (teilweise) eine Position per Gegen-Deal auf die ticket-ID."""
    info = mt5.symbol_info(broker)
    tick = mt5.symbol_info_tick(broker)
    if direction == "long":
        order_type, price = mt5.ORDER_TYPE_SELL, tick.bid
    else:
        order_type, price = mt5.ORDER_TYPE_BUY, tick.ask
    vol = round(volume, _step_decimals(info.volume_step or 0.01))
    if vol < (info.volume_min or 0.01):
        log.warning("[%s] %s close-vol %s < min -> skip", reason, broker, vol)
        return False
    request = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": broker,
        "volume": float(vol), "type": order_type, "position": int(ticket),
        "price": float(price), "deviation": DEVIATION, "magic": MAGIC,
        "comment": f"smc {reason}", "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _filling_mode(info),
    }
    res = mt5.order_send(request)
    ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
    log.info("[%s] %s vol=%s -> %s", reason, broker, vol,
             "OK" if ok else f"FAIL {getattr(res,'comment',mt5.last_error())}")
    if ok:
        notify.trade_closed(broker, reason, f"vol={vol}")
    return ok


def _close_partial(pos: LivePosition, volume: float, reason: str) -> bool:
    return _send_close(pos.broker, pos.ticket, pos.direction, volume, reason)


def _modify_sl(pos: LivePosition, new_sl: float) -> bool:
    request = {
        "action": mt5.TRADE_ACTION_SLTP, "symbol": pos.broker,
        "position": pos.ticket, "sl": float(new_sl), "tp": 0.0,
    }
    res = mt5.order_send(request)
    ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
    log.info("[BE] %s SL->%.5f %s", pos.prefix, new_sl,
             "OK" if ok else f"FAIL {getattr(res,'comment',mt5.last_error())}")
    return ok


def _bars_held(pos: LivePosition) -> int:
    entry = datetime.fromisoformat(pos.entry_time_utc)
    elapsed = (datetime.now(timezone.utc) - entry).total_seconds()
    return int(elapsed // (15 * 60))


def manage_open_positions() -> None:
    """Pro Zyklus: TP1/TP2-Teilschluss, BE-Shift, Max-Hold. SL/BE-Treffer
    erledigt der Broker-Stop automatisch -> verschwundene Tickets ausbuchen."""
    _ensure()
    positions = _load_positions()
    if not positions:
        return

    changed = False
    for prefix in list(positions.keys()):
        pos = positions[prefix]

        # Noch beim Broker offen?
        broker_pos = mt5.positions_get(ticket=pos.ticket)
        if not broker_pos:
            log.info("[CLOSED] %s ticket=%s nicht mehr offen (SL/BE/manuell) -> ausgebucht",
                     prefix, pos.ticket)
            notify.trade_closed(prefix, "SL/BE/manuell", f"ticket {pos.ticket}")
            del positions[prefix]
            changed = True
            continue

        tick = mt5.symbol_info_tick(pos.broker)
        px = tick.bid if pos.direction == "long" else tick.ask

        def _reached(target: float) -> bool:
            return (px >= target) if pos.direction == "long" else (px <= target)

        # TP1 -> 50 % schliessen + SL auf BE
        if not pos.tp1_done and _reached(pos.tp1):
            if _close_partial(pos, pos.lots_initial * TP1_FRAC, "TP1"):
                pos.tp1_done = True
                if _modify_sl(pos, pos.entry_price):
                    pos.be_moved = True
                changed = True

        # TP2 -> weitere 25 % schliessen
        elif pos.tp1_done and not pos.tp2_done and _reached(pos.tp2):
            if _close_partial(pos, pos.lots_initial * TP2_FRAC, "TP2"):
                pos.tp2_done = True
                changed = True

        # Max-Hold -> Rest schliessen
        if _bars_held(pos) >= MAX_HOLD_BARS:
            remaining = broker_pos[0].volume
            if _close_partial(pos, remaining, "HOLD"):
                del positions[prefix]
                changed = True
                continue

        positions[prefix] = pos

    if changed:
        _save_positions(positions)


# ---------------------------------------------------------------------------
# Status / Close-All / kontrollierter Test-Trade
# ---------------------------------------------------------------------------
def status() -> None:
    """Zeigt von uns getrackte Positionen + alle Broker-Positionen mit unserem MAGIC."""
    _ensure()
    tracked = _load_positions()
    print("=== Getrackte Positionen (live_positions.json) ===")
    if not tracked:
        print("  (keine)")
    for k, p in tracked.items():
        print(f"  {k}: ticket={p.ticket} {p.direction} lots={p.lots_initial} "
              f"entry={p.entry_price} SL={p.initial_sl} tp1_done={p.tp1_done} tp2_done={p.tp2_done}")
    print("=== Broker-Positionen mit MAGIC", MAGIC, "===")
    allp = mt5.positions_get() or []
    ours = [p for p in allp if p.magic == MAGIC]
    if not ours:
        print("  (keine)")
    for p in ours:
        side = "long" if p.type == mt5.POSITION_TYPE_BUY else "short"
        print(f"  {p.symbol}: ticket={p.ticket} {side} vol={p.volume} "
              f"open={p.price_open} SL={p.sl} profit={p.profit}")


def close_all() -> None:
    """Schliesst ALLE Positionen mit unserem MAGIC und leert die Registry."""
    _ensure()
    allp = mt5.positions_get() or []
    ours = [p for p in allp if p.magic == MAGIC]
    if not ours:
        print("Keine offenen Positionen mit unserem MAGIC.")
    for p in ours:
        side = "long" if p.type == mt5.POSITION_TYPE_BUY else "short"
        _send_close(p.symbol, p.ticket, side, p.volume, "CLOSE-ALL")
    _save_positions({})
    print("Registry geleert.")


def test_trade(prefix: str, direction: str = "long") -> None:
    """Oeffnet EINEN kontrollierten Test-Trade (echte Order!) auf dem Demo-Konto.
    Entry = aktueller Kurs, SL = Beispiel-Distanz, TP1/TP2 = 2R/4R."""
    from smc_strategy import Setup
    _ensure()
    broker = live_feed.resolve_symbol(prefix)
    if broker is None:
        print(f"Symbol {prefix} nicht gefunden.")
        return
    mt5.symbol_select(broker, True)
    tick = mt5.symbol_info_tick(broker)
    dist = _SAMPLE_SL_DIST.get(prefix, (tick.ask or tick.bid) * 0.002)
    if direction == "long":
        entry = tick.ask
        sl = entry - dist
        tp1, tp2 = entry + 2 * dist, entry + 4 * dist
    else:
        entry = tick.bid
        sl = entry + dist
        tp1, tp2 = entry - 2 * dist, entry - 4 * dist
    setup = Setup(direction=direction, entry_idx=0,
                  entry_time=datetime.now(timezone.utc),
                  entry_price=entry, sl=sl, tp1=tp1, tp2=tp2,
                  zone_kind="TEST", zone_idx=0, zone_low=sl, zone_high=entry,
                  htf_bias="up" if direction == "long" else "down",
                  reason="MANUAL TEST-TRADE")
    print(f"TEST-TRADE {prefix} {direction}: entry={entry} SL={sl} TP1={tp1} TP2={tp2}")
    pos = open_from_setup(prefix, broker, setup, dry=False)
    if pos:
        print(f"OK -> ticket={pos.ticket} lots={pos.lots_initial}. "
              f"Pruefe es in MT5; schliessen mit:  python executor.py --close-all")


def _dry_lot_check() -> None:
    acc = mt5.account_info()
    risk_amount = acc.balance * LIVE_RISK_PCT
    print("=" * 72)
    print(f"  LOT-SIZING DRY-CHECK  (kein Trade)")
    print(f"  Balance ${acc.balance:.2f}  |  Risk {LIVE_RISK_PCT*100:.2f}% = ${risk_amount:.2f}/Trade")
    print("=" * 72)
    for prefix, broker in live_feed.resolve_all().items():
        mt5.symbol_select(broker, True)
        tick = mt5.symbol_info_tick(broker)
        entry = tick.bid if tick and tick.bid else tick.ask
        sl_dist = _SAMPLE_SL_DIST.get(prefix, entry * 0.002)
        try:
            lots, diag = compute_lots(broker, entry, entry - sl_dist, risk_amount)
        except Exception as e:                     # noqa: BLE001
            print(f"\n[{prefix}] FEHLER: {e}")
            continue
        print(f"\n[{prefix}]  ({broker})")
        print(f"  Beispiel-Entry={entry:.5f}  SL={entry - sl_dist:.5f}  (SL-Distanz {sl_dist})")
        print(f"  tick_size={diag['tick_size']}  tick_value=${diag['tick_value']}  "
              f"vol_min={diag['volume_min']}  vol_step={diag['volume_step']}")
        print(f"  -> Lots: {diag['lots']}  (roh {diag['raw_lots']:.4f})")
        print(f"  -> tatsaechliches Risiko bei SL: ${diag['actual_risk']:.2f} "
              f"({diag['actual_risk']/acc.balance*100:.2f}% der Balance)")
        if diag["warn"]:
            print(f"  WARN: {diag['warn']}")
    print("\nFertig - keine Order geschickt.")


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    ap = argparse.ArgumentParser(description="SMC Executor (Phase 6 / M3)")
    ap.add_argument("--status", action="store_true", help="Offene Positionen zeigen")
    ap.add_argument("--manage", action="store_true", help="Einen Management-Zyklus laufen")
    ap.add_argument("--test-trade", metavar="SYMBOL",
                    help="EINEN echten Test-Trade oeffnen (Demo!), z.B. --test-trade EURUSD")
    ap.add_argument("--direction", default="long", choices=["long", "short"],
                    help="Richtung fuer --test-trade (Default long)")
    ap.add_argument("--close-all", action="store_true",
                    help="ALLE Positionen mit unserem MAGIC schliessen")
    args = ap.parse_args()

    _ensure()
    if args.status:
        status()
    elif args.manage:
        manage_open_positions()
        print("Management-Zyklus fertig.")
    elif args.close_all:
        close_all()
    elif args.test_trade:
        test_trade(args.test_trade.upper(), args.direction)
    else:
        _dry_lot_check()        # Default: nur Lot-Sizing, kein Trade

    mt5.shutdown()
