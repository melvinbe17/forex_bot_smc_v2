"""
daily_summary.py  (forex_bot_smc - Phase 6 / Monitoring)
========================================================

Schickt einen Tagesstatus per E-Mail. Liest dafuer NUR die heartbeat.json
(die der Live-Runner schreibt) - KEINE eigene MT5-Verbindung. Dadurch kein
Konflikt um die eine MT5-Python-Verbindung mit dem laufenden Bot.

Ist der Heartbeat zu alt (Bot haengt/aus), wird das oben fett gewarnt.

Gedacht als geplante Windows-Aufgabe (Task Scheduler), z.B. 1x abends.
"""
from __future__ import annotations

import logging
from datetime import datetime

import heartbeat
import notify

log = logging.getLogger("daily_summary")

STALE_AFTER_MIN = 30.0


def _f(v) -> str:
    try:
        return f"{float(v):.0f}"
    except (TypeError, ValueError):
        return str(v)


def build_summary() -> tuple[str, bool]:
    """Gibt (text, stale) zurueck."""
    d = heartbeat.read()
    if d is None:
        return ("Kein Heartbeat gefunden (heartbeat.json fehlt).\n"
                "Laeuft der Bot? Wurde er seit dem Heartbeat-Update neu gestartet?"), True

    age = heartbeat.age_seconds()
    stale = heartbeat.is_stale(STALE_AFTER_MIN)
    age_txt = f"{age/60:.0f} Min" if age is not None else "?"

    lines: list[str] = []
    if stale:
        lines += [f"!!! WARNUNG: Letztes Lebenszeichen vor {age_txt} - "
                  f"der Bot HAENGT oder LAEUFT NICHT !!!", ""]

    lines += [
        f"Letztes Lebenszeichen: vor {age_txt}  (ts {d.get('ts_utc')})",
        f"Modus          : {d.get('mode')}",
        f"Balance        : ${d.get('balance')}",
        f"Equity         : ${d.get('equity')}",
        f"Server-Tag      : {d.get('server_day')}",
        f"Tagesverlust   : ${d.get('daily_loss')}  (Trip ${_f(d.get('daily_trip'))})",
        f"Gesamtverlust  : ${d.get('total_loss')}  (Trip ${_f(d.get('total_trip'))})",
        f"Verluste heute : {d.get('losses_today')} / {d.get('max_losses')}",
        f"Guard HALTED   : {d.get('halted')}  {d.get('halt_reason') or ''}",
        "",
    ]

    pos = d.get("open_positions") or []
    lines.append(f"Offene Positionen ({len(pos)}):")
    if not pos:
        lines.append("  (keine)")
    for p in pos:
        lines.append(f"  {p.get('symbol')} {p.get('side')} vol={p.get('vol')} "
                     f"SL={p.get('sl')} P/L=${p.get('profit')}")

    r = d.get("realized_today")
    if r:
        lines += ["", f"Geschlossene Trades heute: {r.get('count')}  "
                      f"realisiert ${r.get('profit')}"]

    return "\n".join(lines), stale


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    body, stale = build_summary()
    print(body)
    subject = ("WARNUNG Bot haengt? - " if stale else "") + \
              f"Tages-Status {datetime.now().date()}"
    notify.send(subject, body)
