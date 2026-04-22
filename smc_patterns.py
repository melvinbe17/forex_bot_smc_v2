"""
smc_patterns.py  (forex_bot_smc)
================================

Pattern-Detektoren fuer SMC/ICT:

  * Order Blocks  (Bullish/Bearish)
       - "Letzte gegenlaeufige Kerze vor einem Impuls, der ein BOS
          ausloest". Bullish-OB = letzte rote Kerze vor Aufwaerts-BOS.
  * Fair Value Gaps (FVG / Imbalance)
       - 3-Kerzen-Muster: zwischen Kerze[i-1] und Kerze[i+1] bleibt
          ein Price-Gap offen, der als "institutional footprint" gilt.
  * Liquidity Sweeps
       - Wick schiesst kurz ueber ein bekanntes Swing-High (greift die
          Stops der Retail-Longs), Close kommt aber wieder zurueck in
          die Range.

Alle Detektoren liefern dataclass-Listen mit eindeutigen Zonen
(low/high) plus Alter/Mitigations-Status, den die Strategie spaeter
auswerten kann.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

import numpy as np
import pandas as pd

import config
from smc_structure import (
    Swing, SwingType, StructureEvent, EventType, atr,
    find_swings, detect_structure_events,
)


# ======================================================================
# ORDER BLOCKS
# ======================================================================
class OBKind(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


@dataclass
class OrderBlock:
    idx: int                    # Index der OB-Kerze selbst
    timestamp: pd.Timestamp
    kind: OBKind
    low: float
    high: float
    triggered_by_idx: int       # Index der BOS-Kerze
    mitigated_idx: Optional[int] = None
    age_when_mitigated: Optional[int] = None

    @property
    def mid(self) -> float:
        return 0.5 * (self.low + self.high)

    def contains(self, price: float) -> bool:
        return self.low <= price <= self.high


def detect_order_blocks(df: pd.DataFrame,
                        events: Optional[List[StructureEvent]] = None,
                        lookback_bars: Optional[int] = None
                        ) -> List[OrderBlock]:
    """
    Fuer jedes BOS-Event suchen wir rueckwaerts die letzte gegenlaeufige
    Kerze (= Close < Open bei BOS_UP). Ihre Range [low, high] ist der OB.

    CHoCH ignorieren wir hier bewusst - manche Varianten nehmen auch
    CHoCH-OBs, aber das Ergebnis ist dann oft uninvertiert schlechter.
    """
    if events is None:
        events = detect_structure_events(df)
    if lookback_bars is None:
        lookback_bars = config.OB_LOOKBACK_BARS

    obs: List[OrderBlock] = []
    opens = df["Open"].to_numpy()
    closes = df["Close"].to_numpy()
    highs = df["High"].to_numpy()
    lows = df["Low"].to_numpy()

    for ev in events:
        if ev.kind not in (EventType.BOS_UP, EventType.BOS_DOWN, EventType.CHOCH_UP, EventType.CHOCH_DOWN):
            continue
        is_bullish = ev.kind in (EventType.BOS_UP, EventType.CHOCH_UP)
        start = max(0, ev.idx - lookback_bars)
        ob_idx: Optional[int] = None
        for j in range(ev.idx - 1, start - 1, -1):
            # Bullish-OB = letzte rote Kerze vor BOS_UP
            if is_bullish and closes[j] < opens[j]:
                ob_idx = j
                break
            if (not is_bullish) and closes[j] > opens[j]:
                ob_idx = j
                break

        if ob_idx is None:
            continue

        ob = OrderBlock(
            idx=ob_idx,
            timestamp=df.index[ob_idx],
            kind=OBKind.BULLISH if is_bullish else OBKind.BEARISH,
            low=float(lows[ob_idx]),
            high=float(highs[ob_idx]),
            triggered_by_idx=ev.idx,
        )
        obs.append(ob)

    # Mitigation-Check: ab ob.idx+1 pruefen, ab wann die Zone getroffen wurde
    _update_ob_mitigation(df, obs)
    return obs


def _update_ob_mitigation(df: pd.DataFrame, obs: List[OrderBlock]) -> None:
    highs = df["High"].to_numpy()
    lows = df["Low"].to_numpy()
    n = len(df)
    mode = config.OB_MITIGATION
    for ob in obs:
        mid = ob.mid
        for k in range(ob.triggered_by_idx + 1, n):
            if mode == "fifty":
                touched = lows[k] <= mid <= highs[k]
            else:  # "touch"
                touched = not (highs[k] < ob.low or lows[k] > ob.high)
            if touched:
                ob.mitigated_idx = k
                ob.age_when_mitigated = k - ob.idx
                break


def unmitigated_obs(obs: List[OrderBlock], current_idx: int,
                    max_age: Optional[int] = None) -> List[OrderBlock]:
    """OBs zum Zeitpunkt `current_idx` die (a) nicht mitigiert sind
    und (b) nicht zu alt."""
    if max_age is None:
        max_age = config.OB_MAX_AGE_BARS
    out = []
    for ob in obs:
        if ob.triggered_by_idx > current_idx:
            continue
        if ob.mitigated_idx is not None and ob.mitigated_idx <= current_idx:
            continue
        if current_idx - ob.idx > max_age:
            continue
        out.append(ob)
    return out


# ======================================================================
# FAIR VALUE GAPS
# ======================================================================
class FVGKind(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


@dataclass
class FairValueGap:
    idx: int                    # Mittel-Kerze des 3-Bar-Musters
    timestamp: pd.Timestamp
    kind: FVGKind
    low: float
    high: float
    mitigated_idx: Optional[int] = None

    @property
    def size(self) -> float:
        return self.high - self.low

    @property
    def mid(self) -> float:
        return 0.5 * (self.low + self.high)


def detect_fvgs(df: pd.DataFrame,
                min_size_atr: Optional[float] = None,
                atr_series: Optional[pd.Series] = None
                ) -> List[FairValueGap]:
    """
    3-Bar-Imbalance:
      Bullish FVG: high[i-1] < low[i+1]  (Gap zwischen ihnen)
                   -> zone = [high[i-1], low[i+1]]
      Bearish FVG: low[i-1] > high[i+1]
                   -> zone = [high[i+1], low[i-1]]
    """
    if min_size_atr is None:
        min_size_atr = config.FVG_MIN_SIZE_ATR
    if atr_series is None:
        atr_series = atr(df, 14)

    highs = df["High"].to_numpy()
    lows = df["Low"].to_numpy()
    fvgs: List[FairValueGap] = []
    atr_arr = atr_series.to_numpy()

    for i in range(1, len(df) - 1):
        a = atr_arr[i]
        if np.isnan(a) or a <= 0:
            continue
        # Bullish
        if highs[i - 1] < lows[i + 1]:
            size = lows[i + 1] - highs[i - 1]
            if size >= min_size_atr * a:
                fvgs.append(FairValueGap(
                    idx=i, timestamp=df.index[i],
                    kind=FVGKind.BULLISH,
                    low=float(highs[i - 1]), high=float(lows[i + 1]),
                ))
        # Bearish
        elif lows[i - 1] > highs[i + 1]:
            size = lows[i - 1] - highs[i + 1]
            if size >= min_size_atr * a:
                fvgs.append(FairValueGap(
                    idx=i, timestamp=df.index[i],
                    kind=FVGKind.BEARISH,
                    low=float(highs[i + 1]), high=float(lows[i - 1]),
                ))

    _update_fvg_mitigation(df, fvgs)
    return fvgs


def _update_fvg_mitigation(df: pd.DataFrame, fvgs: List[FairValueGap]) -> None:
    """FVG gilt als mitigated wenn Preis die Zone voll schliesst."""
    highs = df["High"].to_numpy()
    lows = df["Low"].to_numpy()
    n = len(df)
    for fvg in fvgs:
        for k in range(fvg.idx + 2, n):
            # Bullish-FVG schliesst wenn Preis unter fvg.low faellt
            if fvg.kind == FVGKind.BULLISH and lows[k] <= fvg.low:
                fvg.mitigated_idx = k; break
            if fvg.kind == FVGKind.BEARISH and highs[k] >= fvg.high:
                fvg.mitigated_idx = k; break


def unmitigated_fvgs(fvgs: List[FairValueGap], current_idx: int,
                     max_age: Optional[int] = None) -> List[FairValueGap]:
    if max_age is None:
        max_age = config.FVG_MAX_AGE_BARS
    out = []
    for fvg in fvgs:
        if fvg.idx + 1 > current_idx:
            continue
        if fvg.mitigated_idx is not None and fvg.mitigated_idx <= current_idx:
            continue
        if current_idx - fvg.idx > max_age:
            continue
        out.append(fvg)
    return out


# ======================================================================
# LIQUIDITY SWEEP
# ======================================================================
class SweepKind(str, Enum):
    BUY_SIDE = "buy_side"        # Wick bricht Swing-High (greift Long-Stops)
    SELL_SIDE = "sell_side"      # Wick bricht Swing-Low


@dataclass
class LiquiditySweep:
    idx: int
    timestamp: pd.Timestamp
    kind: SweepKind
    swept_swing: Swing
    wick_extreme: float          # High bzw. Low der Sweep-Kerze
    close_price: float


def detect_liquidity_sweeps(df: pd.DataFrame,
                            swings: Optional[List[Swing]] = None,
                            atr_series: Optional[pd.Series] = None
                            ) -> List[LiquiditySweep]:
    """
    Fuer jede Kerze pruefen wir, ob sie ein bekanntes Swing-High/Low
    per Wick bricht, aber per Close wieder zurueckkommt.

    Bedingungen:
      * wick >= SWEEP_MIN_WICK_ATR * ATR
      * close kommt zu mindestens SWEEP_MAX_CLOSE_BACK der Range zurueck
    """
    if swings is None:
        swings = find_swings(df)
    if atr_series is None:
        atr_series = atr(df, 14)

    min_wick = config.SWEEP_MIN_WICK_ATR
    close_back = config.SWEEP_MAX_CLOSE_BACK
    lookback = config.SWING_LOOKBACK

    sweeps: List[LiquiditySweep] = []
    highs = df["High"].to_numpy()
    lows = df["Low"].to_numpy()
    closes = df["Close"].to_numpy()
    atr_arr = atr_series.to_numpy()

    for i in range(lookback + 1, len(df)):
        a = atr_arr[i]
        if np.isnan(a) or a <= 0:
            continue

        # Bekannte Swings zum Zeitpunkt i
        known = [s for s in swings if s.idx + lookback <= i and s.idx < i]
        if not known:
            continue

        last_high = next((s for s in reversed(known) if s.kind == SwingType.HIGH), None)
        last_low = next((s for s in reversed(known) if s.kind == SwingType.LOW), None)

        h = highs[i]; l = lows[i]; c = closes[i]

        # Buy-side (Swing-High gesweept)
        if last_high is not None and h > last_high.price:
            wick = h - last_high.price
            # Close zurueck unter den Swing -> sweep bestaetigt
            if wick >= min_wick * a and c <= last_high.price:
                # "wie weit zurueck" - relativ zum wick
                back_ratio = (h - c) / wick if wick > 0 else 0
                if back_ratio >= close_back:
                    sweeps.append(LiquiditySweep(
                        idx=i, timestamp=df.index[i],
                        kind=SweepKind.BUY_SIDE,
                        swept_swing=last_high,
                        wick_extreme=float(h), close_price=float(c),
                    ))
                    continue

        # Sell-side (Swing-Low gesweept)
        if last_low is not None and l < last_low.price:
            wick = last_low.price - l
            if wick >= min_wick * a and c >= last_low.price:
                back_ratio = (c - l) / wick if wick > 0 else 0
                if back_ratio >= close_back:
                    sweeps.append(LiquiditySweep(
                        idx=i, timestamp=df.index[i],
                        kind=SweepKind.SELL_SIDE,
                        swept_swing=last_low,
                        wick_extreme=float(l), close_price=float(c),
                    ))

    return sweeps


# ======================================================================
# Convenience: alles auf einmal analysieren
# ======================================================================
@dataclass
class SMCSnapshot:
    df: pd.DataFrame
    atr: pd.Series
    swings: List[Swing]
    events: List[StructureEvent]
    order_blocks: List[OrderBlock]
    fvgs: List[FairValueGap]
    sweeps: List[LiquiditySweep]

    def summary(self) -> dict:
        return {
            "bars": len(self.df),
            "swings": len(self.swings),
            "bos_up": sum(1 for e in self.events if e.kind == EventType.BOS_UP),
            "bos_down": sum(1 for e in self.events if e.kind == EventType.BOS_DOWN),
            "choch_up": sum(1 for e in self.events if e.kind == EventType.CHOCH_UP),
            "choch_down": sum(1 for e in self.events if e.kind == EventType.CHOCH_DOWN),
            "ob_bullish": sum(1 for o in self.order_blocks if o.kind == OBKind.BULLISH),
            "ob_bearish": sum(1 for o in self.order_blocks if o.kind == OBKind.BEARISH),
            "fvg_bullish": sum(1 for f in self.fvgs if f.kind == FVGKind.BULLISH),
            "fvg_bearish": sum(1 for f in self.fvgs if f.kind == FVGKind.BEARISH),
            "sweeps_buy": sum(1 for s in self.sweeps if s.kind == SweepKind.BUY_SIDE),
            "sweeps_sell": sum(1 for s in self.sweeps if s.kind == SweepKind.SELL_SIDE),
        }


def analyze(df: pd.DataFrame) -> SMCSnapshot:
    """Komplette SMC-Analyse fuer einen Timeframe."""
    a = atr(df, 14)
    sw = find_swings(df, atr_series=a)
    ev = detect_structure_events(df, swings=sw)
    obs = detect_order_blocks(df, events=ev)
    fvgs = detect_fvgs(df, atr_series=a)
    sweeps = detect_liquidity_sweeps(df, swings=sw, atr_series=a)
    return SMCSnapshot(
        df=df, atr=a, swings=sw, events=ev,
        order_blocks=obs, fvgs=fvgs, sweeps=sweeps,
    )
