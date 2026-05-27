#!/usr/bin/env python3
"""
regime_overlay.py
-----------------
Strategie-2-Baustein: Makro-/Regime-Overlay fuer das SMC-Portfolio.

Ziel (vgl. STRATEGY2_HANDOFF.md): MaxDD senken UND Ø-Profit heben, indem
die makro-getriebenen Verlustphasen (v.a. USDJPY 2018-2020, Risk-Off) aus-
gesetzt werden, OHNE die sauberen Edges (EURUSD-Shorts, XAUUSD-Shorts) zu
beruehren.

Daten-Adapterschicht
--------------------
Das Modul nutzt ECHTE Makrodaten, sobald sie unter data/macro/ liegen,
und faellt sonst auf interne Preis-Proxies (nur aus den 3 Kern-Instrumenten)
zurueck. Beide Pfade sind voll 14J-OOS-testbar.

Erwartete FRED-CSVs in data/macro/ (fredgraph.csv-Format: 1. Spalte Datum,
2. Spalte Wert, fehlend = "."):
    DGS10.csv             US 10Y Treasury Yield
    DGS2.csv              US 2Y Treasury Yield
    JPY10Y.csv            Japan 10Y (FRED-ID IRLTLT01JPM156N) -- auch
                          "IRLTLT01JPM156N.csv" wird erkannt
    VIXCLS.csv            VIX (Risk-On/Off)
    DTWEXBGS.csv          Broad USD Index (optional)

Kein Lookahead: alle Regime-Flags werden um 1 Tag verzoegert (shift(1)).
"""
from __future__ import annotations

import glob
import os
from typing import Dict, Optional

import numpy as np
import pandas as pd

MACRO_DIR = "data/macro"
PARQUET = "data/dukascopy/{sym}_M15_15y.parquet"
# Optionaler Live-Frische-Cache (vom VPS via macro_refresh.py geschrieben):
# CSV mit 1. Spalte Datum + je eine Spalte pro Symbol (USDJPY, XAUUSD, ...).
# Wird, falls vorhanden, an die (statische) Parquet-Historie angehaengt,
# damit das Regime im Live-Betrieb aktuelle Tages-Closes nutzt.
RECENT_PX = "data/macro/_daily_px.csv"

# JP-10Y kann unter zwei Dateinamen liegen
_JP10Y_ALIASES = ["JPY10Y", "IRLTLT01JPM156N"]


# --------------------------------------------------------------------------
# Daten laden
# --------------------------------------------------------------------------
def daily_close(sym: str) -> pd.Series:
    """Tages-Close (nur Handelstage). Quelle: 14J-Parquet falls vorhanden,
    sonst allein der Live-Frische-Cache RECENT_PX (vom VPS aus MT5 befuellt).
    Ist Parquet da, wird der Cache angehaengt -> aktuelle Werte ueberschreiben."""
    pq = PARQUET.format(sym=sym)
    if os.path.exists(pq):
        try:
            df = pd.read_parquet(pq)
            s = df["Close"].resample("1D").last().dropna()
        except Exception:
            s = pd.Series(dtype="float64")
    else:
        s = pd.Series(dtype="float64")   # kein Parquet (z.B. Live-VPS) -> nur Cache
    if os.path.exists(RECENT_PX):
        try:
            rc = pd.read_csv(RECENT_PX)
            if sym in rc.columns:
                dcol = rc.columns[0]
                r = pd.Series(pd.to_numeric(rc[sym], errors="coerce").values,
                              index=pd.to_datetime(rc[dcol], errors="coerce")).dropna()
                r.index = r.index.normalize()
                s = pd.concat([s, r])
                s = s[~s.index.duplicated(keep="last")].sort_index()
        except Exception:
            pass  # fail-open: bei Cache-Problem nur Parquet nutzen
    return s


def load_macro() -> Dict[str, pd.Series]:
    """Liest alle FRED-CSVs aus data/macro/. Leer, wenn nichts vorhanden."""
    out: Dict[str, pd.Series] = {}
    if not os.path.isdir(MACRO_DIR):
        return out
    for path in sorted(glob.glob(os.path.join(MACRO_DIR, "*.csv"))):
        name = os.path.splitext(os.path.basename(path))[0].upper()
        try:
            df = pd.read_csv(path)
        except Exception:
            continue
        if df.shape[1] < 2:
            continue
        dcol, vcol = df.columns[0], df.columns[1]
        idx = pd.to_datetime(df[dcol], errors="coerce")
        val = pd.to_numeric(df[vcol], errors="coerce")  # "." -> NaN
        s = pd.Series(val.values, index=idx).dropna()
        s = s[~s.index.isna()]
        out[name] = s.sort_index()
    return out


def _get(macro: Dict[str, pd.Series], *names) -> Optional[pd.Series]:
    for n in names:
        if n in macro:
            return macro[n]
    return None


# --------------------------------------------------------------------------
# Regime-Konstruktion
# --------------------------------------------------------------------------
def build_regime(sma_win: int = 200,
                 carry_win: int = 120,
                 vix_win: int = 120,
                 vix_mult: float = 1.3) -> pd.DataFrame:
    """
    Baut eine Tages-Tabelle mit Regime-Signalen.

    Spalten u.a.:
        risk_off_proxy : interner Proxy (Yen-bid & Gold-bid)
        risk_off       : finales Flag (Makro wenn vorhanden, sonst Proxy)
        risk_off_lag   : risk_off um 1 Tag verzoegert  <-- fuer Gating nutzen
        has_macro      : (DataFrame.attrs) ob echte Makrodaten benutzt wurden
    """
    jpy = daily_close("USDJPY")
    xau = daily_close("XAUUSD")
    R = pd.DataFrame({"usdjpy": jpy, "xau": xau}).dropna()

    # --- Interner Preis-Proxy (immer berechnet) ---------------------------
    R["jpy_sma"] = R["usdjpy"].rolling(sma_win).mean()
    R["xau_sma"] = R["xau"].rolling(sma_win).mean()
    # Risk-Off = Yen gesucht (USDJPY unter Trend) UND Gold gesucht (ueber Trend)
    R["risk_off_proxy"] = (R["usdjpy"] < R["jpy_sma"]) & (R["xau"] > R["xau_sma"])

    macro = load_macro()
    has_macro = False

    if macro:
        # Renditedifferenz US-JP (Carry-Proxy fuer USDJPY)
        us10 = _get(macro, "DGS10")
        jp10 = _get(macro, *_JP10Y_ALIASES)
        if us10 is not None and jp10 is not None:
            diff = (us10.reindex(R.index, method="ffill")
                    - jp10.reindex(R.index, method="ffill"))
            R["us_jp_10y"] = diff
            # Carry verschlechtert sich = Differenz faellt unter eigenen Schnitt
            R["carry_falling"] = diff < diff.rolling(carry_win).mean()
            has_macro = True

        # VIX-Spike-Regime (Risk-Off)
        vix = _get(macro, "VIXCLS", "VIX")
        if vix is not None:
            v = vix.reindex(R.index, method="ffill")
            R["vix"] = v
            R["risk_off_vix"] = v > (v.rolling(vix_win).median() * vix_mult)
            has_macro = True

        cols = [c for c in ["carry_falling", "risk_off_vix"] if c in R.columns]
        # Makro-Risk-Off: Carry faellt UND/ODER VIX-Spike, zusaetzlich durch
        # den internen Proxy bestaetigt (reduziert Fehlsignale in guten Jahren)
        if cols:
            R["risk_off"] = R[cols].any(axis=1) & R["risk_off_proxy"]
        else:
            R["risk_off"] = R["risk_off_proxy"]
    else:
        R["risk_off"] = R["risk_off_proxy"]

    R["risk_off_lag"] = R["risk_off"].shift(1).fillna(False).astype(bool)
    R.attrs["has_macro"] = has_macro
    return R


# --------------------------------------------------------------------------
# Gating
# --------------------------------------------------------------------------
def regime_at(regime: pd.DataFrame, times: pd.Series,
              col: str = "risk_off_lag") -> pd.Series:
    """Mappt das (verzoegerte) Regime auf Trade-Zeitpunkte (per Tagesdatum)."""
    daily = regime[col]
    d = pd.to_datetime(times).dt.normalize()
    return d.map(daily).fillna(False).astype(bool)


def apply_gate(trades: pd.DataFrame,
               regime: pd.DataFrame,
               time_col: str = "close_time",
               symbol_col: str = "symbol",
               side_col: str = "side",
               gate_symbols=("USDJPY",),
               gate_sides=None) -> pd.DataFrame:
    """
    Gibt die Trades zurueck, die NACH dem Gate uebrig bleiben.

    Validierte Default-Regel: USDJPY BEIDE Seiten im Risk-Off-Regime aussetzen
    (gate_sides=None). 2018-2020 bluteten beide Richtungen — daher beidseitig.
    gate_sides=("long",) -> nur die Long-Seite gaten.
    """
    ro = regime_at(regime, trades[time_col])
    sym_hit = trades[symbol_col].isin(gate_symbols)
    if gate_sides is None:
        side_hit = True
    else:
        side_hit = trades[side_col].str.lower().isin([s.lower() for s in gate_sides])
    drop = sym_hit & side_hit & ro
    return trades.loc[~drop].copy()


if __name__ == "__main__":
    reg = build_regime()
    print(f"Makrodaten genutzt: {reg.attrs['has_macro']}")
    print(f"Regime-Tage: {len(reg)}  ({reg.index.min().date()} .. {reg.index.max().date()})")
    yr = reg.groupby(reg.index.year)["risk_off_lag"].mean().round(2)
    print("Anteil Risk-Off-Tage pro Jahr:")
    print(yr.to_string())
