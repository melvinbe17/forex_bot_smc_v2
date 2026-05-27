"""
smc_strategy.py  (forex_bot_smc)
================================

Multi-Timeframe SMC-Entry-Logik fuer Backtests.

Ansatz (erste Baseline-Version, bewusst simpel gehalten)
--------------------------------------------------------
1) HTF-Bias (H1, aus M15 resampled):
     - Letzter Struktur-Event BOS_UP / CHoCH_UP     -> Bias "up"   (nur Longs)
     - Letzter Struktur-Event BOS_DOWN / CHoCH_DOWN -> Bias "down" (nur Shorts)
     - Keine Events bislang                         -> "neutral"   (kein Trade)

2) LTF-Setup (M15, an jedem Bar-Close):
     - Bei "up":   Long-Kandidat, wenn aktuelle Bar eine unmitigierte
                   Bullish-OB oder Bullish-FVG tagged (Low <= zone.high)
                   UND die Zone vollstaendig im DISCOUNT der aktuellen
                   M15-PD-Zone liegt (zone.high <= PD-Equilibrium).
     - Bei "down": spiegelbildlich (Bearish-Zone im PREMIUM).

3) Entry-Trigger:
     - Long:  Bar ist bullish (Close > Open) UND hat die Zone getagged.
     - Short: Bar ist bearish (Close < Open) UND hat die Zone getagged.
     - Entry-Price = Bar-Close. Erste Naeherung - spaeter ggf. Limit Order
       auf Zone-High/Low verschieben.

4) Risk:
     - SL:   Long  = zone.low  * (1 - SL_BUFFER_PCT)
             Short = zone.high * (1 + SL_BUFFER_PCT)
     - TP1 = Entry +/- R_TP1 * |Entry - SL|   (Default 2R)
     - TP2 = Entry +/- R_TP2 * |Entry - SL|   (Default 4R)

No-Lookahead: saemtliche Berechnungen nutzen nur snap-Daten mit
.idx < aktuellem LTF-Index bzw. HTF-Events mit .timestamp <= LTF-Bar-Zeit.

Dieses Modul produziert nur Setup-Objekte. Trade-Management (TP1-Teilclose,
BE-Shift, Max-Hold, FTMO-Daily-Loss) macht der Backtester (backtest_m15.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from ct_adx_filter import compute_h4_indicators, should_block_setup_at_time

from smc_structure import SwingType, EventType
from smc_patterns import (
    analyze, OBKind, FVGKind,
    unmitigated_obs, unmitigated_fvgs,
)

try:
    import config as _cfg
except Exception:                     # Fallback wenn config fehlt
    _cfg = None


# ---------------------------------------------------------------------------
# Parameter  (Defaults, ggf. in config.py auslagern)
# ---------------------------------------------------------------------------
SL_BUFFER_PCT = 0.0005      # 5 Pip Buffer bei EURUSD
R_TP1 = 2.0
R_TP2 = 4.0
MIN_WARMUP_BARS = 50        # warten bis genug Swings/Zonen vorhanden


# ---------------------------------------------------------------------------
# Session-Filter
# ---------------------------------------------------------------------------
def _session_ok(ts: pd.Timestamp, symbol: Optional[str] = None) -> bool:
    """True wenn der Bar-Timestamp in einer erlaubten Killzone liegt.
    Benutzt config.py (SESSION_FILTER_ENABLED / KILLZONES / SKIP_*).
    Wenn symbol gesetzt, werden zusaetzlich die Per-Symbol-Dead-Zone-Blacklists
    geprueft (SESSION_HOUR_BLACKLIST_PER_SYMBOL / SESSION_WEEKDAY_BLACKLIST_PER_SYMBOL).
    Wenn config fehlt oder Filter aus -> immer True.
    """
    if _cfg is None or not getattr(_cfg, "SESSION_FILTER_ENABLED", False):
        return True

    # pandas Timestamp ist timezone-aware (UTC) oder naive (UTC by convention
    # fuer Dukascopy-CSV). Wir arbeiten mit hour/weekday direkt.
    wd = ts.weekday()
    skip_wd = getattr(_cfg, "SESSION_SKIP_WEEKDAYS", [])
    if wd in skip_wd:
        return False

    skip_fri_after = getattr(_cfg, "SESSION_SKIP_FRIDAY_AFTER_UTC", None)
    if wd == 4 and skip_fri_after is not None and ts.hour >= skip_fri_after:
        return False

    # Per-Symbol Dead-Zone Blacklists (datenbasiert, Stand 22.04.2026)
    if symbol is not None:
        hour_bl = getattr(_cfg, "SESSION_HOUR_BLACKLIST_PER_SYMBOL", {}).get(symbol, [])
        if ts.hour in hour_bl:
            return False
        wd_bl = getattr(_cfg, "SESSION_WEEKDAY_BLACKLIST_PER_SYMBOL", {}).get(symbol, [])
        if wd in wd_bl:
            return False

    kz = getattr(_cfg, "SESSION_KILLZONES_UTC", None)
    if not kz:
        return True
    h = ts.hour
    for start, end in kz:
        if start <= h < end:
            return True
    return False


# ---------------------------------------------------------------------------
# Vola-Regime-Filter  (High-Vola-Phasen ausschliessen)
# ---------------------------------------------------------------------------
def compute_htf_vola_skip_mask(
    ltf_df: pd.DataFrame,
    htf_df: pd.DataFrame,
    atr_period: int = 14,
    median_window: int = 200,
    max_ratio: float = 1.3,
) -> np.ndarray:
    """Per-LTF-Bar Boolean-Maske: True = Hochvola-Regime = SKIP.

    Berechnet ATR(atr_period) auf HTF, vergleicht mit rolling-Median(median_window).
    Wenn HTF-ATR > max_ratio * Median -> Bar als High-Vola markiert.
    Dann wird fuer jeden LTF-Bar der letzte bekannte HTF-Flag per forward-fill
    uebernommen (kein Lookahead, weil reindex(method="ffill") nur rueckwaerts schaut).

    Warmup: solange weniger als median_window HTF-Bars existieren, ist
    die Maske False (= nicht skippen) - das ist bewusst, damit wir am
    Anfang des Datensatzes nicht alle Entries verlieren.
    """
    if len(htf_df) < atr_period + 1:
        return np.zeros(len(ltf_df), dtype=bool)

    high = htf_df["High"].astype(float)
    low = htf_df["Low"].astype(float)
    close = htf_df["Close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(atr_period, min_periods=atr_period).mean()
    med = atr.rolling(median_window, min_periods=median_window).median()
    htf_skip = (atr > max_ratio * med).fillna(False)

    # Forward-Fill HTF-Flag auf LTF-Index (letzter bekannter HTF-Bar <= LTF-Bar)
    reindexed = htf_skip.reindex(ltf_df.index, method="ffill").fillna(False)
    return reindexed.to_numpy(dtype=bool)


# ---------------------------------------------------------------------------
# D1-Bias-Filter  (Makro-Regime per Daily EMA)
# ---------------------------------------------------------------------------
def compute_d1_bias_mask(
    ltf_df: pd.DataFrame,
    ema_period: int = 50,
) -> np.ndarray:
    """Per-LTF-Bar Bias-Array: "up" | "down" | "neutral".

    Resampled LTF (M15) auf D1, berechnet EMA(ema_period) auf D1-Close,
    vergleicht D1-Close mit EMA, mappt per ffill zurueck auf LTF-Index.
    Kein Lookahead: reindex(method="ffill") holt nur den letzten D1-Bar
    mit timestamp <= LTF-Bar-timestamp.

    Warmup: die ersten ~ema_period Tage sind "neutral" (EMA noch nicht
    berechenbar) - in dieser Zeit werden alle Setups geskippt.
    """
    agg = {
        "Open": "first", "High": "max", "Low": "min",
        "Close": "last", "Volume": "sum",
    }
    d1 = ltf_df.resample("1D", label="right", closed="right").agg(agg).dropna()
    if len(d1) < ema_period + 1:
        return np.full(len(ltf_df), "neutral", dtype=object)

    close = d1["Close"].astype(float)
    ema = close.ewm(span=ema_period, adjust=False,
                    min_periods=ema_period).mean()

    bias_d1 = pd.Series("neutral", index=d1.index, dtype=object)
    bias_d1[close > ema] = "up"
    bias_d1[close < ema] = "down"
    # EMA-NaN (Warmup) bleibt explizit "neutral"
    bias_d1[ema.isna()] = "neutral"

    bias_ltf = bias_d1.reindex(ltf_df.index, method="ffill").fillna("neutral")
    return bias_ltf.to_numpy(dtype=object)


# ---------------------------------------------------------------------------
# Setup-Datentyp
# ---------------------------------------------------------------------------
@dataclass
class Setup:
    direction: str           # "long" | "short"
    entry_idx: int           # LTF-Bar-Index des Triggers
    entry_time: pd.Timestamp
    entry_price: float
    sl: float
    tp1: float
    tp2: float
    zone_kind: str           # "OB" | "FVG"
    zone_idx: int
    zone_low: float
    zone_high: float
    htf_bias: str            # "up" | "down"
    reason: str = ""


# ---------------------------------------------------------------------------
# HTF-Bias
# ---------------------------------------------------------------------------
_BULL_EVENTS = {EventType.BOS_UP, EventType.CHOCH_UP}
_BEAR_EVENTS = {EventType.BOS_DOWN, EventType.CHOCH_DOWN}


def htf_bias_at(htf_events, at_time: pd.Timestamp) -> str:
    """HTF-Bias zum Zeitpunkt at_time.
    Nutzt nur HTF-Events mit .timestamp <= at_time.
    """
    last_kind = None
    for ev in htf_events:
        if ev.timestamp > at_time:
            break
        last_kind = ev.kind
    if last_kind in _BULL_EVENTS:
        return "up"
    if last_kind in _BEAR_EVENTS:
        return "down"
    return "neutral"


# ---------------------------------------------------------------------------
# PD-Zone (vereinfacht) an einem bestimmten LTF-Index
# ---------------------------------------------------------------------------
def pd_zone_at(swings, current_idx: int):
    """Vereinfachte PD-Zone aus letztem Swing-High + letztem Swing-Low
    mit .idx < current_idx.
    Rueckgabe: (low, high, equilibrium) oder None.
    """
    last_high = None
    last_low = None
    for s in swings:
        if s.idx >= current_idx:
            break
        if s.kind == SwingType.HIGH:
            last_high = s.price
        else:
            last_low = s.price
    if last_high is None or last_low is None:
        return None
    if last_high <= last_low:
        return None
    return (last_low, last_high, (last_low + last_high) / 2.0)


# ---------------------------------------------------------------------------
# Setup-Finder (ein Bar)
# ---------------------------------------------------------------------------
def find_setup(
    ltf_df: pd.DataFrame,
    ltf_snap,
    htf_events,
    current_idx: int,
    symbol: Optional[str] = None,
) -> Optional[Setup]:
    """Prueft ob am LTF-Bar mit Index current_idx ein Setup vorliegt.
    symbol (optional) aktiviert Per-Symbol-Dead-Zone-Blacklists im Session-Filter.
    """
    if current_idx < MIN_WARMUP_BARS:
        return None

    bar = ltf_df.iloc[current_idx]
    bar_time = ltf_df.index[current_idx]

    # Session-Filter vor allem anderen - spart die teuren OB/FVG-Scans.
    if not _session_ok(bar_time, symbol):
        return None

    bias = htf_bias_at(htf_events, bar_time)
    if bias == "neutral":
        return None

    # Direction-Filter per Config (Long/Short einzeln deaktivierbar)
    trade_longs = getattr(_cfg, "TRADE_LONGS", True) if _cfg else True
    trade_shorts = getattr(_cfg, "TRADE_SHORTS", True) if _cfg else True
    if bias == "up" and not trade_longs:
        return None
    if bias == "down" and not trade_shorts:
        return None

    pdz = pd_zone_at(ltf_snap.swings, current_idx)
    if pdz is None:
        return None
    pd_low, pd_high, pd_eq = pdz

    # Aktive Zonen VOR dem aktuellen Bar (kein Lookahead)
    # Zone-Typen per Config ein-/ausschaltbar (Default OB=False, FVG=True).
    use_ob = getattr(_cfg, "ZONE_USE_OB", True) if _cfg else True
    use_fvg = getattr(_cfg, "ZONE_USE_FVG", True) if _cfg else True
    obs = unmitigated_obs(ltf_snap.order_blocks, current_idx - 1) if use_ob else []
    fvgs = unmitigated_fvgs(ltf_snap.fvgs, current_idx - 1) if use_fvg else []

    o, h, l, c = bar["Open"], bar["High"], bar["Low"], bar["Close"]

    if bias == "up":
        # nur Bullish-Zonen im Discount
        candidates = []
        for ob in obs:
            if ob.kind != OBKind.BULLISH:
                continue
            if ob.high > pd_eq:
                continue
            if l <= ob.high and h >= ob.low:
                candidates.append(("OB", ob))
        for fvg in fvgs:
            if fvg.kind != FVGKind.BULLISH:
                continue
            if fvg.high > pd_eq:
                continue
            if l <= fvg.high and h >= fvg.low:
                candidates.append(("FVG", fvg))

        if not candidates:
            return None
        if c <= o:                 # keine bullische Confirmation-Bar
            return None

        # Tiefste Zone zuerst (am weitesten im Discount)
        candidates.sort(key=lambda x: x[1].low)
        kind, zone = candidates[0]
        entry = float(c)
        sl = float(zone.low) * (1 - SL_BUFFER_PCT)
        risk = entry - sl
        if risk <= 0:
            return None
        tp1 = entry + R_TP1 * risk
        tp2 = entry + R_TP2 * risk
        return Setup(
            direction="long",
            entry_idx=current_idx,
            entry_time=bar_time,
            entry_price=entry,
            sl=sl, tp1=tp1, tp2=tp2,
            zone_kind=kind,
            zone_idx=zone.idx,
            zone_low=float(zone.low),
            zone_high=float(zone.high),
            htf_bias=bias,
            reason=f"HTF up + {kind} Discount tag + bull close",
        )

    else:  # bias == "down"
        candidates = []
        for ob in obs:
            if ob.kind != OBKind.BEARISH:
                continue
            if ob.low < pd_eq:
                continue
            if h >= ob.low and l <= ob.high:
                candidates.append(("OB", ob))
        for fvg in fvgs:
            if fvg.kind != FVGKind.BEARISH:
                continue
            if fvg.low < pd_eq:
                continue
            if h >= fvg.low and l <= fvg.high:
                candidates.append(("FVG", fvg))

        if not candidates:
            return None
        if c >= o:
            return None

        candidates.sort(key=lambda x: -x[1].high)
        kind, zone = candidates[0]
        entry = float(c)
        sl = float(zone.high) * (1 + SL_BUFFER_PCT)
        risk = sl - entry
        if risk <= 0:
            return None
        tp1 = entry - R_TP1 * risk
        tp2 = entry - R_TP2 * risk
        return Setup(
            direction="short",
            entry_idx=current_idx,
            entry_time=bar_time,
            entry_price=entry,
            sl=sl, tp1=tp1, tp2=tp2,
            zone_kind=kind,
            zone_idx=zone.idx,
            zone_low=float(zone.low),
            zone_high=float(zone.high),
            htf_bias=bias,
            reason=f"HTF down + {kind} Premium tag + bear close",
        )


# ---------------------------------------------------------------------------
# Sammelt Setups ueber den kompletten LTF-DF
# ---------------------------------------------------------------------------
def find_all_setups(
    ltf_df: pd.DataFrame,
    ltf_snap,
    htf_snap,
    htf_df: Optional[pd.DataFrame] = None,
    symbol: Optional[str] = None,
) -> List[Setup]:
    """Iteriert Bar fuer Bar und sammelt Setups.
    WARNUNG: auf 185k Bars kann das einige Minuten dauern (Python-Schleife
    ueber OB-/FVG-Listen). Erste Tests mit --limit 5000 machen.

    htf_df (optional) wird fuer den VOLA_REGIME_FILTER gebraucht. Wenn None
    oder Filter deaktiviert, wird nicht gefiltert. Backtester und Live-Runner
    sollten htf_df immer mitliefern.

    symbol (optional) wird an find_setup weitergereicht und aktiviert dort die
    Per-Symbol-Dead-Zone-Blacklists im Session-Filter.
    """
    setups: List[Setup] = []
    htf_events = htf_snap.events

    # Vola-Regime-Filter (optional, per config)
    vola_skip = None
    if (_cfg is not None
            and getattr(_cfg, "VOLA_REGIME_FILTER_ENABLED", False)
            and htf_df is not None):
        vola_skip = compute_htf_vola_skip_mask(
            ltf_df, htf_df,
            atr_period=getattr(_cfg, "VOLA_REGIME_ATR_PERIOD", 14),
            median_window=getattr(_cfg, "VOLA_REGIME_MEDIAN_WINDOW", 200),
            max_ratio=getattr(_cfg, "VOLA_REGIME_MAX_RATIO", 1.3),
        )

    # D1-Bias-Filter (optional, per config)
    d1_bias = None
    if (_cfg is not None
            and getattr(_cfg, "D1_BIAS_FILTER_ENABLED", False)):
        d1_bias = compute_d1_bias_mask(
            ltf_df,
            ema_period=getattr(_cfg, "D1_BIAS_EMA_PERIOD", 50),
        )

    # ---------------------------------------------------------------
    # CT/ADX-Filter (H4) - blockiert Counter-Trend-Trades bei mittlerem ADX.
    # H4 wird aus M15 intern resampled (unabhaengig von HTF=H1 im Rest des Bots).
    # ---------------------------------------------------------------
    ct_adx_h4 = None
    if (_cfg is not None
            and getattr(_cfg, "CT_ADX_FILTER_ENABLED", False)):
        from data_loader import resample as _resample
        h4_src = _resample(ltf_df, "H4")
        ct_adx_h4 = compute_h4_indicators(
            h4_src,
            ema_fast=getattr(_cfg, "CT_ADX_H4_EMA_FAST", 20),
            ema_slow=getattr(_cfg, "CT_ADX_H4_EMA_SLOW", 50),
            adx_n  =getattr(_cfg, "CT_ADX_H4_ADX_PERIOD", 14),
            slope_lb=getattr(_cfg, "CT_ADX_H4_SLOPE_LB", 3),
        )
    ct_adx_blocked = 0

    # ---------------------------------------------------------------
    # Regime-Overlay (Strategie 2): makro-getriebene Setups im Risk-Off-
    # Regime aussetzen (Default: USDJPY beide Seiten). Symbol-agnostisches
    # Tages-Signal aus regime_overlay.build_regime() (Carry/VIX + Proxy).
    # Fail-open: bei Fehler/fehlenden Daten wird nicht gegatet.
    # ---------------------------------------------------------------
    regime_ro = None          # daily bool Series (risk_off_lag), index normalisiert
    regime_sides = None
    if (_cfg is not None
            and getattr(_cfg, "REGIME_OVERLAY_ENABLED", False)
            and symbol in getattr(_cfg, "REGIME_GATE_SYMBOLS", [])):
        try:
            import regime_overlay as _ro
            _reg = _ro.build_regime()
            regime_ro = _reg["risk_off_lag"].copy()
            regime_ro.index = regime_ro.index.normalize()
            regime_sides = getattr(_cfg, "REGIME_GATE_SIDES", None)
        except Exception as e:
            print(f"[REGIME] WARN: Regime nicht ladbar ({e}) -> kein Gate")
            regime_ro = None
    regime_blocked = 0

    for i in range(len(ltf_df)):
        if vola_skip is not None and vola_skip[i]:
            continue
        # D1-Bias-Gate: neutral (Warmup) -> kein Trade
        if d1_bias is not None and d1_bias[i] == "neutral":
            continue
        s = find_setup(ltf_df, ltf_snap, htf_events, i, symbol=symbol)
        if s is None:
            continue
        # Direction muss zum D1-Regime passen
        if d1_bias is not None:
            bias = d1_bias[i]
            if s.direction == "long" and bias != "up":
                continue
            if s.direction == "short" and bias != "down":
                continue
        # CT/ADX-Filter: skip Counter-Trend bei mittlerem ADX (15..25)
        if ct_adx_h4 is not None:
            if should_block_setup_at_time(
                ct_adx_h4,
                s.direction,
                s.entry_time,
                adx_min=getattr(_cfg, "CT_ADX_MIN_BLOCK", 15.0),
                adx_max=getattr(_cfg, "CT_ADX_MAX_BLOCK", 25.0),
            ):
                ct_adx_blocked += 1
                continue
        # Regime-Overlay-Gate: Risk-Off -> Setup aussetzen
        if regime_ro is not None:
            d = s.entry_time.normalize()
            if bool(regime_ro.get(d, False)) and (
                    regime_sides is None or s.direction in regime_sides):
                regime_blocked += 1
                continue
        setups.append(s)

    if ct_adx_h4 is not None:
        print(f"[CT_ADX] blocked={ct_adx_blocked}  kept={len(setups)}")
    if regime_ro is not None:
        print(f"[REGIME] risk-off blocked={regime_blocked}  kept={len(setups)}")
    return setups


# ---------------------------------------------------------------------------
# CLI zum schnellen Test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    from data_loader import load_symbol, resample

    ap = argparse.ArgumentParser(
        description="Testlauf Setup-Finder (ohne Backtest)."
    )
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="M15")
    ap.add_argument("--htf", default="H1")
    ap.add_argument("--source", default="csv",
                    choices=["auto", "csv", "yfinance"])
    ap.add_argument("--limit", type=int, default=5000,
                    help="Nur letzte N LTF-Bars (Default 5000 ~ 2 Monate M15). "
                         "Zum Testen gedacht - volle 185k Bars dauern lang.")
    args = ap.parse_args()

    print(f"Lade {args.symbol} {args.tf} (source={args.source}) ...")
    ltf_df = load_symbol(args.symbol, timeframes=[args.tf],
                         source=args.source)[args.tf]
    if args.limit and args.limit > 0:
        ltf_df = ltf_df.iloc[-args.limit:]
    print(f"  -> {len(ltf_df)} {args.tf}-Bars "
          f"({ltf_df.index[0]} -> {ltf_df.index[-1]})")

    print(f"Resample nach {args.htf} ...")
    htf_df = resample(ltf_df, args.htf)
    print(f"  -> {len(htf_df)} {args.htf}-Bars")

    print("Analysiere HTF-Struktur ...")
    htf_snap = analyze(htf_df)
    print("Analysiere LTF-Struktur ...")
    ltf_snap = analyze(ltf_df)

    print("Suche Setups ...")
    setups = find_all_setups(ltf_df, ltf_snap, htf_snap, htf_df=htf_df, symbol=args.symbol)

    longs = sum(1 for s in setups if s.direction == "long")
    shorts = len(setups) - longs
    ob_setups = sum(1 for s in setups if s.zone_kind == "OB")
    fvg_setups = len(setups) - ob_setups

    print("=" * 78)
    print(f"  SETUP-SUMMARY  {args.symbol} {args.tf}  "
          f"({len(ltf_df)} Bars -> {len(setups)} Setups)")
    print("=" * 78)
    print(f"  Long / Short       : {longs} / {shorts}")
    print(f"  Zone OB / FVG      : {ob_setups} / {fvg_setups}")
    print(f"  Setups per 1000 Bar: {len(setups) / len(ltf_df) * 1000:.1f}")

    if setups:
        print("\n  Erste 5 Setups:")
        for s in setups[:5]:
            print(f"    {s.entry_time}  {s.direction:5}  "
                  f"E={s.entry_price:.5f}  SL={s.sl:.5f}  "
                  f"TP1={s.tp1:.5f}  TP2={s.tp2:.5f}  [{s.zone_kind}]")
        print("\n  Letzte 5 Setups:")
        for s in setups[-5:]:
            print(f"    {s.entry_time}  {s.direction:5}  "
                  f"E={s.entry_price:.5f}  SL={s.sl:.5f}  "
                  f"TP1={s.tp1:.5f}  TP2={s.tp2:.5f}  [{s.zone_kind}]")
