#!/usr/bin/env python3
"""
ct_adx_filter.py  (forex_bot_smc)
==================================
Counter-Trend ADX Filter - blockiert Setups im toxischen CT-Bucket.

Validierungs-Ergebnis (baseline_0.7pct, 1158 trades, 5J-Backtest):
  F3 (skip CT & 15<=ADX<25):
    Return:  +60.92pp  (143.22% -> 204.14%)
    MaxDD:   -3.98pp   (-17.14% -> -13.15%)
    Alle 6 echten Killer-Monate abgeschwaecht.
    Normal-Monate netto +17.21R (robuster Netto-Effekt)

Logik:
  - H4-Trend:         ema20 vs ema50 + slope
  - Counter-Trend:    long & H4_downtrend  OR  short & H4_uptrend
  - Toxic Bucket:     CT AND 15 <= H4_ADX < 25  -> BLOCKEN
  - Durchlassen:      With-Trend, CT mit ADX<15, CT mit ADX>=25 (profitabel!)

Design:
  - Self-contained: resampled intern M15 -> H4.
  - Wird aus smc_strategy.find_all_setups() aufgerufen (gleiche Ebene
    wie vola_skip + d1_bias).
  - NaN-safe: ADX=NaN -> nicht blocken (Evidenz-basiert).
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
#  Defaults - werden von smc_strategy via _cfg ueberschrieben
# ---------------------------------------------------------------------------
DEFAULT_ADX_MIN_BLOCK = 15.0
DEFAULT_ADX_MAX_BLOCK = 25.0
DEFAULT_EMA_FAST      = 20
DEFAULT_EMA_SLOW      = 50
DEFAULT_ADX_PERIOD    = 14
DEFAULT_SLOPE_LB      = 3


# ---------------------------------------------------------------------------
#  Wilder-Smoothing (identisch zu killer_month_trend_analysis.py)
# ---------------------------------------------------------------------------
def _wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


# ---------------------------------------------------------------------------
#  Indikatoren auf H4 (akzeptiert ltf-Schema: DatetimeIndex + Open/High/Low/Close)
# ---------------------------------------------------------------------------
def compute_h4_indicators(h4_df: pd.DataFrame,
                          ema_fast: int = DEFAULT_EMA_FAST,
                          ema_slow: int = DEFAULT_EMA_SLOW,
                          adx_n:    int = DEFAULT_ADX_PERIOD,
                          slope_lb: int = DEFAULT_SLOPE_LB) -> pd.DataFrame:
    """
    Erwartet: DataFrame mit DatetimeIndex + Columns "Open","High","Low","Close"
    (wie aus data_loader.resample). Liefert dasselbe DF plus Columns:
      ema20, ema50, ema_slope, adx14, trend_up, trend_dn
    """
    out = h4_df.copy().sort_index()

    o, h, l, c = out["Open"], out["High"], out["Low"], out["Close"]

    out["ema20"]     = c.ewm(span=ema_fast, adjust=False).mean()
    out["ema50"]     = c.ewm(span=ema_slow, adjust=False).mean()
    out["ema_slope"] = out["ema20"].diff(slope_lb)

    # True Range
    prev_c = c.shift(1)
    tr = pd.concat([
        h - l,
        (h - prev_c).abs(),
        (l - prev_c).abs(),
    ], axis=1).max(axis=1)

    # Directional movement
    up_move   = h.diff()
    down_move = -l.diff()
    plus_dm   = np.where((up_move   > down_move) & (up_move   > 0),
                         up_move, 0.0)
    minus_dm  = np.where((down_move > up_move)   & (down_move > 0),
                         down_move, 0.0)

    atr      = _wilder(tr, adx_n)
    plus_di  = 100.0 * _wilder(pd.Series(plus_dm,  index=out.index), adx_n) / atr
    minus_di = 100.0 * _wilder(pd.Series(minus_dm, index=out.index), adx_n) / atr
    dx       = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    out["adx14"] = _wilder(dx, adx_n)

    out["trend_up"] = (out["ema20"] > out["ema50"]) & (out["ema_slope"] > 0)
    out["trend_dn"] = (out["ema20"] < out["ema50"]) & (out["ema_slope"] < 0)
    return out


# ---------------------------------------------------------------------------
#  Lookup: H4-State am Entry-Zeitpunkt (letzter H4-Bar <= entry_time)
# ---------------------------------------------------------------------------
def _lookup_h4_state(h4_ind: pd.DataFrame,
                     entry_time: pd.Timestamp
                     ) -> Tuple[bool, bool, float]:
    """Liefert (trend_up, trend_dn, adx14) fuer letzten H4-Bar <= entry_time.
       (False, False, NaN) wenn kein H4-Bar verfuegbar (Warmup).
    """
    # searchsorted-right - 1 = groesster Index <= entry_time
    idx_arr = h4_ind.index.to_numpy()
    pos = np.searchsorted(idx_arr, np.datetime64(entry_time), side="right") - 1
    if pos < 0:
        return False, False, float("nan")
    row = h4_ind.iloc[pos]
    return bool(row["trend_up"]), bool(row["trend_dn"]), float(row["adx14"])


# ---------------------------------------------------------------------------
#  Core-Entscheidung
# ---------------------------------------------------------------------------
def _is_counter_trend(direction: str, tu: bool, td: bool) -> bool:
    d = direction.lower()
    if d == "short" and tu: return True
    if d == "long"  and td: return True
    return False


def should_block_setup(direction: str,
                       trend_up:  bool,
                       trend_dn:  bool,
                       adx14:     float,
                       adx_min:   float = DEFAULT_ADX_MIN_BLOCK,
                       adx_max:   float = DEFAULT_ADX_MAX_BLOCK) -> bool:
    """True = Setup blocken, False = durchlassen.
       NaN ADX -> nicht blocken (wir blocken nur bei Evidenz).
    """
    if np.isnan(adx14):
        return False
    if not _is_counter_trend(direction, trend_up, trend_dn):
        return False
    return adx_min <= adx14 < adx_max


def should_block_setup_at_time(h4_ind: pd.DataFrame,
                               direction: str,
                               entry_time: pd.Timestamp,
                               adx_min: float = DEFAULT_ADX_MIN_BLOCK,
                               adx_max: float = DEFAULT_ADX_MAX_BLOCK) -> bool:
    """Convenience-Wrapper: Lookup H4-State am entry_time + Block-Check."""
    tu, td, adx = _lookup_h4_state(h4_ind, entry_time)
    return should_block_setup(direction, tu, td, adx, adx_min, adx_max)


# ---------------------------------------------------------------------------
#  Self-Test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    cases = [
        # direction, tu,    td,    adx,   expected_block,  comment
        ("short",    True,  False, 18.0,  True,  "CT + mid ADX -> block"),
        ("short",    True,  False, 12.0,  False, "CT + low ADX -> pass"),
        ("short",    True,  False, 28.0,  False, "CT + high ADX -> pass (profitable)"),
        ("long",     False, True,  20.0,  True,  "CT (long in downtrend) -> block"),
        ("long",     True,  False, 20.0,  False, "With-trend -> pass"),
        ("short",    False, False, 20.0,  False, "No H4 trend -> pass"),
        ("short",    True,  False, float("nan"), False, "NaN ADX -> pass"),
        ("long",     False, True,  15.0,  True,  "CT + ADX=15 (boundary lo) -> block"),
        ("long",     False, True,  24.99, True,  "CT + ADX=24.99 -> block"),
        ("long",     False, True,  25.0,  False, "CT + ADX=25 (boundary hi) -> pass"),
    ]
    print("ct_adx_filter self-test:")
    ok = 0
    for d, tu, td, adx, exp, note in cases:
        got = should_block_setup(d, tu, td, adx)
        mark = "OK" if got == exp else "FAIL"
        if got == exp: ok += 1
        adx_s = f"{adx:6.2f}" if not np.isnan(adx) else "   NaN"
        print(f"  {d:<5} tu={str(tu):<5} td={str(td):<5} adx={adx_s}  "
              f"expected={str(exp):<5} got={str(got):<5} [{mark}]  {note}")
    print(f"\n  {ok}/{len(cases)} cases passed")