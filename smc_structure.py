"""
smc_structure.py  (forex_bot_smc)
=================================

Markt-Struktur-Detektoren fuer SMC/ICT:

  * ATR-Baseline
  * Swing Highs / Swing Lows   (N-Bar-Fractal)
  * BOS (Break of Structure)   - Trend-Fortsetzung
  * CHoCH (Change of Character) - Trend-Wechsel
  * Premium/Discount Zones     - 50%-Fib des letzten Impulses

Konventionen:
  * df: pandas DataFrame mit Columns Open/High/Low/Close, DatetimeIndex
  * Alle Detektoren geben "signal-at-index" zurueck - die Entscheidung,
    ob wirklich ein BOS vorliegt, wird am Close der Kerze getroffen
    (kein Lookahead).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

import numpy as np
import pandas as pd

import config


# ----------------------------------------------------------------------
# ATR
# ----------------------------------------------------------------------
def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    high = df["High"]; low = df["Low"]; close = df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


# ----------------------------------------------------------------------
# SWINGS
# ----------------------------------------------------------------------
class SwingType(str, Enum):
    HIGH = "high"
    LOW = "low"


@dataclass
class Swing:
    idx: int                # Integer-Position im DataFrame
    timestamp: pd.Timestamp
    price: float
    kind: SwingType

    def __repr__(self) -> str:  # nur fuer Debug
        return f"Swing({self.kind.value}@{self.timestamp.date()} {self.price:.5f})"


def find_swings(df: pd.DataFrame,
                lookback: Optional[int] = None,
                min_distance_atr: Optional[float] = None,
                atr_series: Optional[pd.Series] = None) -> List[Swing]:
    """
    Fractal-basierte Swing-Detection.
    Ein Swing-High bei Index i ist definiert als:
        high[i] > max(high[i-lookback..i-1])  AND
        high[i] > max(high[i+1..i+lookback])
    Analog fuer Swing-Low.

    Hinweis: wegen des rechten Fensters bestaetigt sich ein Swing erst
    `lookback` Bars spaeter (kein Lookahead, solange wir den Swing erst
    ab Bar i+lookback als "bekannt" behandeln).
    """
    if lookback is None:
        lookback = config.SWING_LOOKBACK
    if min_distance_atr is None:
        min_distance_atr = config.MIN_SWING_DISTANCE_ATR
    if atr_series is None:
        atr_series = atr(df, 14)

    highs = df["High"].to_numpy()
    lows = df["Low"].to_numpy()
    n = len(df)
    swings: List[Swing] = []

    for i in range(lookback, n - lookback):
        win_h = highs[i - lookback:i + lookback + 1]
        win_l = lows[i - lookback:i + lookback + 1]
        center = lookback  # Position von i im Fenster

        # Swing-High: Zentrum ist echter Max
        if win_h[center] == win_h.max() and \
           (win_h[center] > win_h[:center]).all() and \
           (win_h[center] > win_h[center + 1:]).all():
            swings.append(Swing(
                idx=i,
                timestamp=df.index[i],
                price=float(highs[i]),
                kind=SwingType.HIGH,
            ))
            continue

        # Swing-Low
        if win_l[center] == win_l.min() and \
           (win_l[center] < win_l[:center]).all() and \
           (win_l[center] < win_l[center + 1:]).all():
            swings.append(Swing(
                idx=i,
                timestamp=df.index[i],
                price=float(lows[i]),
                kind=SwingType.LOW,
            ))

    # Kleine Swings ausfiltern (unter min_distance_atr)
    if min_distance_atr and min_distance_atr > 0 and swings:
        filtered: List[Swing] = []
        for s in swings:
            a = float(atr_series.iloc[s.idx]) if not np.isnan(atr_series.iloc[s.idx]) else 0.0
            if a <= 0:
                filtered.append(s)
                continue
            if not filtered:
                filtered.append(s)
                continue
            prev = filtered[-1]
            if abs(s.price - prev.price) < min_distance_atr * a:
                # zu dicht -> ignorieren, ausser es ist ein Typwechsel
                if s.kind == prev.kind:
                    # selben Typ? ueberschreiben wenn "extremer"
                    if s.kind == SwingType.HIGH and s.price > prev.price:
                        filtered[-1] = s
                    elif s.kind == SwingType.LOW and s.price < prev.price:
                        filtered[-1] = s
                    continue
            filtered.append(s)
        swings = filtered

    return swings


# ----------------------------------------------------------------------
# BOS / CHoCH
# ----------------------------------------------------------------------
class EventType(str, Enum):
    BOS_UP = "bos_up"        # Trend-Fortsetzung long (letzter SH gebrochen)
    BOS_DOWN = "bos_down"    # Trend-Fortsetzung short (letzter SL gebrochen)
    CHOCH_UP = "choch_up"    # Wechsel bearish->bullish (letzter SH gebrochen)
    CHOCH_DOWN = "choch_down"  # Wechsel bullish->bearish (letzter SL gebrochen)


@dataclass
class StructureEvent:
    idx: int
    timestamp: pd.Timestamp
    kind: EventType
    break_price: float           # welcher Swing wurde gebrochen
    trigger_swing: Swing         # der gebrochene Swing
    close_price: float           # Close der ausloesenden Kerze


def _trend_from_swings(swings: List[Swing]) -> Optional[int]:
    """
    Trend aus den letzten 2 gleichartigen Swings ableiten.
    Bullish (+1): HH und HL.  Bearish (-1): LH und LL.
    Gibt None zurueck, wenn nicht eindeutig.
    """
    if len(swings) < 4:
        return None
    last_highs = [s for s in swings if s.kind == SwingType.HIGH][-2:]
    last_lows = [s for s in swings if s.kind == SwingType.LOW][-2:]
    if len(last_highs) < 2 or len(last_lows) < 2:
        return None
    hh = last_highs[-1].price > last_highs[-2].price
    hl = last_lows[-1].price > last_lows[-2].price
    lh = last_highs[-1].price < last_highs[-2].price
    ll = last_lows[-1].price < last_lows[-2].price
    if hh and hl:
        return +1
    if lh and ll:
        return -1
    return None


def detect_structure_events(df: pd.DataFrame,
                            swings: Optional[List[Swing]] = None,
                            confirmation: Optional[str] = None
                            ) -> List[StructureEvent]:
    """
    Geht chronologisch durch und meldet BOS/CHoCH.

    Logik (bar-by-bar, kein Lookahead):
      * Bei jeder neuen Bar: welche Swings sind *bestaetigt* bekannt?
        Nur Swings mit swing.idx + SWING_LOOKBACK <= aktueller Bar.
      * Aktueller Trend = aus letzten bekannten HH/HL/LH/LL.
      * Bullish-Trend + Close bricht letztes bekanntes SH -> BOS_UP
      * Bullish-Trend + Close bricht letztes bekanntes SL -> CHOCH_DOWN
      * Symmetrisch fuer Bearish-Trend.

    confirmation:
      "close" - nur Close triggert Event
      "wick"  - auch High/Low triggert
    """
    if swings is None:
        swings = find_swings(df)
    if confirmation is None:
        confirmation = config.BOS_CONFIRMATION

    lookback = config.SWING_LOOKBACK
    dedup_bars = getattr(config, "EVENT_DEDUP_BARS", 0)
    dedup_tol = getattr(config, "EVENT_DEDUP_PRICE_TOL_ATR", 0.0)
    atr_arr = atr(df, 14).to_numpy()
    events: List[StructureEvent] = []
    n = len(df)

    # Wir iterieren vorwaerts und halten eine "bekannte" Swing-Liste
    last_event_trend: Optional[int] = None   # Trend nach letztem Event

    for i in range(lookback + 1, n):
        # Bekannte Swings: alle mit swing.idx + lookback <= i
        known = [s for s in swings if s.idx + lookback <= i]
        if len(known) < 2:
            continue

        last_high = next((s for s in reversed(known) if s.kind == SwingType.HIGH), None)
        last_low = next((s for s in reversed(known) if s.kind == SwingType.LOW), None)
        if last_high is None or last_low is None:
            continue

        # Trend bestimmen: erst last_event_trend nutzen, sonst aus swings
        trend = last_event_trend if last_event_trend is not None \
                                 else _trend_from_swings(known)
        if trend is None:
            continue

        row = df.iloc[i]
        test_high = row["Close"] if confirmation == "close" else row["High"]
        test_low = row["Close"] if confirmation == "close" else row["Low"]

        ev: Optional[StructureEvent] = None

        if trend > 0:
            # Bullish: Break-Up = Fortsetzung; Break-Down = CHoCH
            if test_high > last_high.price and last_high.idx < i:
                ev = StructureEvent(
                    idx=i, timestamp=df.index[i], kind=EventType.BOS_UP,
                    break_price=last_high.price, trigger_swing=last_high,
                    close_price=float(row["Close"]),
                )
            elif test_low < last_low.price and last_low.idx < i:
                ev = StructureEvent(
                    idx=i, timestamp=df.index[i], kind=EventType.CHOCH_DOWN,
                    break_price=last_low.price, trigger_swing=last_low,
                    close_price=float(row["Close"]),
                )
                last_event_trend = -1
        else:
            if test_low < last_low.price and last_low.idx < i:
                ev = StructureEvent(
                    idx=i, timestamp=df.index[i], kind=EventType.BOS_DOWN,
                    break_price=last_low.price, trigger_swing=last_low,
                    close_price=float(row["Close"]),
                )
            elif test_high > last_high.price and last_high.idx < i:
                ev = StructureEvent(
                    idx=i, timestamp=df.index[i], kind=EventType.CHOCH_UP,
                    break_price=last_high.price, trigger_swing=last_high,
                    close_price=float(row["Close"]),
                )
                last_event_trend = +1

        if ev is not None:
            # 1) Gleicher Event auf demselben Swing? skip.
            if events and events[-1].kind == ev.kind and \
               events[-1].trigger_swing.idx == ev.trigger_swing.idx:
                continue
            # 2) Gleicher Event-Typ, nahe Preis, nahe Zeit? skip.
            if events and dedup_bars > 0:
                prev = events[-1]
                if prev.kind == ev.kind and (i - prev.idx) <= dedup_bars:
                    a = atr_arr[i] if not np.isnan(atr_arr[i]) else 0.0
                    if a > 0 and abs(ev.break_price - prev.break_price) \
                            <= dedup_tol * a:
                        continue
            events.append(ev)
            if last_event_trend is None:
                last_event_trend = trend

    return events


# ----------------------------------------------------------------------
# Premium / Discount  (50%-Fib des letzten Impulses)
# ----------------------------------------------------------------------
@dataclass
class PDZone:
    from_idx: int
    to_idx: int
    low: float
    high: float
    equilibrium: float     # 50%

    @property
    def discount_top(self) -> float:
        """Oberes Ende der Discount-Zone (= equilibrium)."""
        return self.equilibrium

    @property
    def premium_bottom(self) -> float:
        """Unteres Ende der Premium-Zone (= equilibrium)."""
        return self.equilibrium

    def zone_for(self, direction: int) -> tuple[float, float]:
        """
        direction=+1 -> Discount (low..equilibrium)
        direction=-1 -> Premium  (equilibrium..high)
        """
        if direction > 0:
            return (self.low, self.equilibrium)
        return (self.equilibrium, self.high)


def current_pd_zone(swings: List[Swing]) -> Optional[PDZone]:
    """
    Bestimmt die aktuelle Premium/Discount-Zone aus dem juengsten
    Impuls (letztes Swing-Low -> letztes Swing-High oder umgekehrt).
    """
    if len(swings) < 2:
        return None
    last_high = next((s for s in reversed(swings) if s.kind == SwingType.HIGH), None)
    last_low = next((s for s in reversed(swings) if s.kind == SwingType.LOW), None)
    if last_high is None or last_low is None:
        return None

    frm, to = (last_low, last_high) if last_low.idx < last_high.idx else (last_high, last_low)
    low = min(frm.price, to.price)
    high = max(frm.price, to.price)
    eq = low + config.PD_EQUILIBRIUM * (high - low)
    return PDZone(
        from_idx=frm.idx, to_idx=to.idx,
        low=low, high=high, equilibrium=eq,
    )
