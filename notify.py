"""
notify.py  (forex_bot_smc - Phase 6 / M5)
=========================================

E-Mail-Alerts via Gmail SMTP. Wird vom Executor (Trade auf/zu) und vom
FTMO-Guard (Halt) aufgerufen.

Aktivierung
-----------
E-Mail ist AN, sobald die Umgebungsvariable mit dem Gmail-App-Passwort
gesetzt ist (Name aus config.EMAIL_PASSWORD_ENV_VAR, Default GMAIL_APP_PASSWORD).
So bleibt das Passwort aus dem Code/Repo raus.

Auf dem VPS (einmalig, PowerShell):
    setx GMAIL_APP_PASSWORD "deinapppasswort"
... dann eine NEUE PowerShell oeffnen (setx wirkt erst in neuen Sessions).

Robustheit
----------
Versand-Fehler werden geloggt, aber NIE nach oben geworfen - ein E-Mail-
Problem darf den Handel niemals stoppen.
"""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

import config

log = logging.getLogger("notify")

_PWD_VAR = getattr(config, "EMAIL_PASSWORD_ENV_VAR", "GMAIL_APP_PASSWORD")


def _password() -> str:
    return os.environ.get(_PWD_VAR, "")


def enabled() -> bool:
    """E-Mail aktiv, wenn das App-Passwort als Env-Var gesetzt ist."""
    return bool(_password())


def send(subject: str, body: str) -> bool:
    """Schickt eine E-Mail. Gibt True bei Erfolg zurueck, sonst False
    (loggt nur, wirft nie)."""
    if not enabled():
        log.debug("E-Mail aus (%s nicht gesetzt) -> skip: %s", _PWD_VAR, subject)
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = f"[SMC-Bot] {subject}"
        msg["From"] = config.EMAIL_FROM
        msg["To"] = config.EMAIL_TO
        msg.set_content(body)
        ctx = ssl.create_default_context()
        with smtplib.SMTP(config.EMAIL_SMTP_HOST, config.EMAIL_SMTP_PORT, timeout=15) as s:
            s.starttls(context=ctx)
            s.login(config.EMAIL_FROM, _password())
            s.send_message(msg)
        log.info("E-Mail gesendet: %s", subject)
        return True
    except Exception as e:                           # noqa: BLE001
        log.error("E-Mail-Versand fehlgeschlagen (%s): %s", subject, e)
        return False


# ---------------------------------------------------------------------------
# Convenience-Wrapper fuer die Bot-Events
# ---------------------------------------------------------------------------
def trade_opened(prefix: str, direction: str, lots: float,
                 entry: float, sl: float, tp1: float, tp2: float,
                 ticket: int) -> None:
    send(
        f"OPEN {prefix} {direction.upper()} {lots} Lots",
        f"Symbol : {prefix}\nRichtung: {direction}\nLots   : {lots}\n"
        f"Entry  : {entry}\nSL     : {sl}\nTP1    : {tp1}\nTP2    : {tp2}\n"
        f"Ticket : {ticket}",
    )


def trade_closed(symbol: str, reason: str, detail: str = "") -> None:
    send(f"CLOSE {symbol} ({reason})",
         f"Symbol: {symbol}\nGrund : {reason}\n{detail}")


def guard_halt(reason: str) -> None:
    send("!!! FTMO-GUARD HALT !!!",
         f"Der Bot hat aus FTMO-Risikogruenden GESTOPPT.\n\nGrund: {reason}\n\n"
         f"Alle offenen Positionen werden geschlossen. Keine neuen Entries "
         f"bis zum Reset / naechsten Handelstag.")


# ---------------------------------------------------------------------------
# CLI: Test-Mail
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    ap = argparse.ArgumentParser(description="notify: E-Mail-Test")
    ap.add_argument("--test", action="store_true", help="Test-Mail verschicken")
    args = ap.parse_args()

    print(f"E-Mail aktiv: {enabled()}  (Env-Var: {_PWD_VAR})")
    print(f"Von: {config.EMAIL_FROM}  An: {config.EMAIL_TO}  "
          f"SMTP: {config.EMAIL_SMTP_HOST}:{config.EMAIL_SMTP_PORT}")
    if args.test:
        ok = send("Test-Alert",
                  "Wenn du das liest, funktionieren die Bot-Benachrichtigungen. ✅")
        print(f"Gesendet: {ok}")
    else:
        print("Tipp: 'python notify.py --test' schickt eine Test-Mail.")
