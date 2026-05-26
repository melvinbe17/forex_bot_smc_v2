"""
heartbeat.py  (forex_bot_smc - Phase 6: Monitoring)
===================================================

Kleine Status-/Lebenszeichen-Datei (heartbeat.json), die der Live-Runner
bei jedem Zyklus schreibt. Andere Tools (daily_summary, watchdog) lesen nur
diese Datei - OHNE eigene MT5-Verbindung. So gibt es keinen Konflikt um die
eine MT5-Python-Verbindung, und ein veralteter Zeitstempel verraet sofort,
wenn der Bot haengt oder abgestuerzt ist.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("heartbeat")

HEARTBEAT_FILE = Path(__file__).with_name("heartbeat.json")


def write(data: dict) -> None:
    payload = dict(data)
    payload["ts_utc"] = datetime.now(timezone.utc).isoformat()
    try:
        HEARTBEAT_FILE.write_text(json.dumps(payload, indent=2))
    except Exception as e:                           # noqa: BLE001
        log.error("Heartbeat schreiben fehlgeschlagen: %s", e)


def read() -> dict | None:
    if not HEARTBEAT_FILE.exists():
        return None
    try:
        return json.loads(HEARTBEAT_FILE.read_text())
    except Exception as e:                           # noqa: BLE001
        log.error("Heartbeat lesen fehlgeschlagen: %s", e)
        return None


def age_seconds() -> float | None:
    """Alter des letzten Heartbeats in Sekunden, oder None wenn nicht lesbar."""
    d = read()
    if not d or "ts_utc" not in d:
        return None
    try:
        ts = datetime.fromisoformat(d["ts_utc"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except Exception:                                # noqa: BLE001
        return None


def is_stale(max_age_min: float = 30.0) -> bool:
    """True wenn kein Heartbeat existiert oder er aelter als max_age_min ist."""
    a = age_seconds()
    return a is None or a > max_age_min * 60
