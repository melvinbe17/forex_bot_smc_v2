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
    """Waehlt den vom Symbol erlaubten Filling-Modus (FOK > IOC > RETURN)."""
    fm = getattr(info, "filling_mode", 0)
    if fm & mt5.SYMBOL_FILLING_FOK:
        return mt5.ORDER_FILLING_FOK
    if fm & mt5.SYMBOL_FILLING_IOC:
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

    pos = LivePosition(
        prefix=prefix, broker=broker, ticket=int(result.order),
        direction=setup.direction, entry_price=float(result.price),
        initial_sl=float(setup.sl), tp1=float(setup.tp1), tp2=float(setup.tp2),
        lots_initial=float(lots),
        entry_time_utc=datetime.now(timezone.utc).isoformat(),
    )
    positions = _load_positions()
    positions[prefix] = pos
    _save_positions(positions)
    log.info("[OPEN] %s %s  ticket=%s  lots=%s  @%.5f  SL=%.5f",
             prefix, setup.direction, pos.ticket, lots, pos.entry_price, pos.initial_sl)
    return pos


# ---------------------------------------------------------------------------
# Teilschliessung / SL-Modify / Management
# ---------------------------------------------------------------------------
def _close_partial(pos: LivePosition, volume: float, reason: str) -> bool:
    info = mt5.symbol_info(pos.broker)
    tick = mt5.symbol_info_tick(pos.broker)
    if pos.direction == "long":
        order_type, price = mt5.ORDER_TYPE_SELL, tick.bid
    else:
        order_type, price = mt5.ORDER_TYPE_BUY, tick.ask
    vol = round(volume, _step_decimals(info.volume_step or 0.01))
    if vol < (info.volume_min or 0.01):
        return False
    request = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": pos.broker,
        "volume": float(vol), "type": order_type, "position": pos.ticket,
        "price": float(price), "deviation": DEVIATION, "magic": MAGIC,
        "comment": f"smc {reason}", "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _filling_mode(info),
    }
    res = mt5.order_send(request)
    ok = res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
    log.info("[%s] %s vol=%s -> %s", reason, pos.prefix, vol,
             "OK" if ok else f"FAIL {getattr(res,'comment',mt5.last_error())}")
    return ok


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
# Standalone: NUR Lot-Sizing-Dry-Check (kein Trade)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    _ensure()
    acc = mt5.account_info()
    risk_amount = acc.balance * LIVE_RISK_PCT

    print("=" * 72)
    print(f"  LOT-SIZING DRY-CHECK  (kein Trade)")
    print(f"  Balance ${acc.balance:.2f}  |  Risk {LIVE_RISK_PCT*100:.2f}% = ${risk_amount:.2f}/Trade")
    print("=" * 72)

    mapping = live_feed.resolve_all()
    for prefix, broker in mapping.items():
        mt5.symbol_select(broker, True)
        tick = mt5.symbol_info_tick(broker)
        entry = tick.bid if tick and tick.bid else tick.ask
        sl_dist = _SAMPLE_SL_DIST.get(prefix, entry * 0.002)
        sl = entry - sl_dist                       # Beispiel: Long
        try:
            lots, diag = compute_lots(broker, entry, sl, risk_amount)
        except Exception as e:                     # noqa: BLE001
            print(f"\n[{prefix}] FEHLER: {e}")
            continue
        print(f"\n[{prefix}]  ({broker})")
        print(f"  Beispiel-Entry={entry:.5f}  SL={sl:.5f}  (SL-Distanz {sl_dist})")
        print(f"  tick_size={diag['tick_size']}  tick_value=${diag['tick_value']}  "
              f"vol_min={diag['volume_min']}  vol_step={diag['volume_step']}")
        print(f"  -> Lots: {diag['lots']}  (roh {diag['raw_lots']:.4f})")
        print(f"  -> tatsaechliches Risiko bei SL: ${diag['actual_risk']:.2f} "
              f"({diag['actual_risk']/acc.balance*100:.2f}% der Balance)")
        if diag["warn"]:
            print(f"  WARN: {diag['warn']}")

    mt5.shutdown()
    print("\nFertig - keine Order geschickt.")
