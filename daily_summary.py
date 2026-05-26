"""
daily_summary.py  (forex_bot_smc - Phase 6 / Monitoring)
========================================================

Sammelt einen Tagesstatus und schickt ihn per E-Mail (notify):
  - Balance / Equity
  - FTMO-Guard: Tages-/Gesamtverlust gegen die Trip-Schwellen, Halt-Status
  - Offene Positionen (unsere, per MAGIC)
  - Heute geschlossene Trades + realisierter P/L

Gedacht als geplante Windows-Aufgabe (Task Scheduler), z.B. 1x abends.
Laeuft unabhaengig vom Live-Runner (eigene MT5-Verbindung, danach shutdown).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

import live_feed
import ftmo_guard
import notify
from executor import MAGIC

log = logging.getLogger("daily_summary")


def build_summary() -> str:
    acc = mt5.account_info()
    snap = ftmo_guard.snapshot()

    lines = [
        f"Balance        : ${acc.balance:.2f}",
        f"Equity         : ${acc.equity:.2f}",
        f"Server-Tag      : {snap['server_day']}",
        f"Tagesverlust   : ${snap['daily_loss']:.2f}  "
        f"(Trip ${snap['daily_trip']:.0f} / hart ${snap['daily_hard']:.0f})",
        f"Gesamtverlust  : ${snap['total_loss']:.2f}  "
        f"(Trip ${snap['total_trip']:.0f} / hart ${snap['total_hard']:.0f})",
        f"Verluste heute : {snap['losses_today']} / {snap['max_losses']}",
        f"Guard HALTED   : {snap['halted']}  {snap['halt_reason']}",
        "",
    ]

    # Offene Positionen (unsere)
    allp = mt5.positions_get() or []
    ours = [p for p in allp if p.magic == MAGIC]
    lines.append(f"Offene Positionen ({len(ours)}):")
    if not ours:
        lines.append("  (keine)")
    for p in ours:
        side = "long" if p.type == mt5.POSITION_TYPE_BUY else "short"
        lines.append(f"  {p.symbol} {side} vol={p.volume} open={p.price_open} "
                     f"SL={p.sl} P/L=${p.profit:.2f}")

    # Heute geschlossene Trades
    try:
        now = ftmo_guard._server_now().replace(tzinfo=None)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        deals = mt5.history_deals_get(day_start, now + timedelta(hours=1)) or []
        closed = [d for d in deals
                  if d.magic == MAGIC and d.entry == mt5.DEAL_ENTRY_OUT]
        realized = sum(d.profit for d in closed)
        lines += ["", f"Geschlossene Trades heute: {len(closed)}  "
                      f"realisiert ${realized:.2f}"]
    except Exception as e:                           # noqa: BLE001
        lines += ["", f"(Deals heute nicht lesbar: {e})"]

    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    live_feed.ensure_initialized()
    body = build_summary()
    print(body)
    notify.send(f"Tages-Status {datetime.now().date()}", body)
    mt5.shutdown()
