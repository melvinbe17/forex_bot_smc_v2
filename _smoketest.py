"""
_smoketest.py  (forex_bot_smc)
==============================

Offline-Smoketest fuer die SMC-Detektoren. Laeuft OHNE yfinance
und OHNE echte CSV - erzeugt synthetische OHLC-Daten mit
bekannten Strukturen und prueft dass die Detektoren sie finden.

Run:
    python3 _smoketest.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config
from smc_structure import (
    atr, find_swings, detect_structure_events,
    SwingType, EventType, current_pd_zone,
)
from smc_patterns import (
    analyze, OBKind, FVGKind, SweepKind,
    detect_order_blocks, detect_fvgs, detect_liquidity_sweeps,
)


# ----------------------------------------------------------------------
# Synthetic OHLC mit kontrollierter Struktur
# ----------------------------------------------------------------------
def make_bullish_then_reversal(n: int = 400, seed: int = 1) -> pd.DataFrame:
    """
    Baut:
      1) einen klaren Bull-Trend (HH/HL) fuer ~60% der Bars
      2) einen Liquidity-Sweep am Hoch
      3) ein CHoCH gefolgt von Bear-Trend
    So dass wir garantiert sowohl BOS_UP, CHoCH_DOWN, Sweeps und
    Order-Blocks drin haben.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01 09:00", periods=n, freq="15min")
    price = 100.0
    data = []
    # Phase 1: Bull-Trend (zigzag aufwaerts)
    trend_end = int(n * 0.55)
    for i in range(trend_end):
        drift = 0.15 if (i // 6) % 2 == 0 else -0.07
        step = drift + rng.normal(0, 0.08)
        o = price
        c = price + step
        h = max(o, c) + abs(rng.normal(0, 0.07))
        l = min(o, c) - abs(rng.normal(0, 0.07))
        data.append((o, h, l, c))
        price = c

    # Phase 2: Sweep am Top (grosses Wick nach oben, Close zurueck)
    top = max(d[1] for d in data)
    o = price
    h = top + 1.2          # klarer Sweep-Wick
    l = price - 0.2
    c = top - 0.5          # Close unter dem Swing-High
    data.append((o, h, l, c))
    price = c

    # Phase 3: Reversal runter (zigzag abwaerts)
    for i in range(n - trend_end - 1):
        drift = -0.18 if (i // 6) % 2 == 0 else 0.08
        step = drift + rng.normal(0, 0.08)
        o = price
        c = price + step
        h = max(o, c) + abs(rng.normal(0, 0.07))
        l = min(o, c) - abs(rng.normal(0, 0.07))
        data.append((o, h, l, c))
        price = c

    df = pd.DataFrame(data, columns=["Open", "High", "Low", "Close"],
                      index=idx)
    df["Volume"] = 0.0
    return df


def make_fvg_bars(seed: int = 2) -> pd.DataFrame:
    """Kleine Serie mit einem eindeutigen Bullish-FVG in der Mitte."""
    rng = np.random.default_rng(seed)
    n = 40
    idx = pd.date_range("2024-06-01 09:00", periods=n, freq="15min")
    price = 100.0
    rows = []
    for i in range(n):
        step = rng.normal(0, 0.05)
        o = price; c = price + step
        h = max(o, c) + 0.05; l = min(o, c) - 0.05
        rows.append([o, h, l, c])
        price = c

    # Mitte: impuls nach oben der einen Gap oeffnet
    mid = 20
    # bar mid-1: normal
    # bar mid:   monster-impuls (close high)
    # bar mid+1: Low muss > bar mid-1 High sein
    rows[mid - 1] = [100.0, 100.2, 99.9, 100.1]
    rows[mid]     = [100.1, 101.0, 100.0, 100.95]
    rows[mid + 1] = [100.95, 101.3, 100.60, 101.1]   # Low=100.60 > 100.2

    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"],
                      index=idx)
    df["Volume"] = 0.0
    return df


# Test-Instruments-Map fuer zukuenftige Tests
test_instruments = {
    "SYNTH1": {"spread": 0.0, "pip": 0.01, "category": "index"},
}


# ----------------------------------------------------------------------
# Assertions-Helper
# ----------------------------------------------------------------------
checks_passed = True

def check(desc: str, cond: bool, detail: str = "") -> None:
    global checks_passed
    status = "PASS" if cond else "FAIL"
    if not cond:
        checks_passed = False
    print(f"  [{status}] {desc}{('  ' + detail) if detail else ''}")


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------
def test_structure() -> None:
    print("\n--- TEST 1: Structure (Bull -> Sweep -> Bear) ---")
    df = make_bullish_then_reversal(n=400, seed=1)
    snap = analyze(df)
    s = snap.summary()
    for k, v in s.items():
        print(f"    {k:<22} {v}")

    check("mind. 10 Swings erkannt", s["swings"] >= 10, f"got {s['swings']}")
    check("mind. 1 BOS_UP", s["bos_up"] >= 1, f"got {s['bos_up']}")
    check("mind. 1 CHoCH_DOWN (oder BOS_DOWN) nach Reversal",
          s["choch_down"] + s["bos_down"] >= 1,
          f"choch_down={s['choch_down']} bos_down={s['bos_down']}")
    check("mind. 1 Bullish-OB", s["ob_bullish"] >= 1,
          f"got {s['ob_bullish']}")
    check("mind. 1 Bearish-OB nach Reversal", s["ob_bearish"] >= 1,
          f"got {s['ob_bearish']}")
    check("mind. 1 Buy-side Sweep (am Top)", s["sweeps_buy"] >= 1,
          f"got {s['sweeps_buy']}")

    pdz = current_pd_zone(snap.swings)
    check("Premium/Discount-Zone berechenbar", pdz is not None)
    if pdz:
        check("PD low < equilibrium < high",
              pdz.low < pdz.equilibrium < pdz.high,
              f"{pdz.low:.3f} < {pdz.equilibrium:.3f} < {pdz.high:.3f}")


def test_fvg() -> None:
    print("\n--- TEST 2: Fair Value Gap ---")
    df = make_fvg_bars()
    # FVG_MIN_SIZE_ATR haengt vom ATR ab - fuer den kleinen Chart
    # explizit klein halten
    fvgs = detect_fvgs(df, min_size_atr=0.0)
    print(f"    {len(fvgs)} FVG(s) erkannt")
    for f in fvgs:
        print(f"      {f.kind.value}  {f.low:.3f} .. {f.high:.3f} "
              f"@{f.timestamp}  mitigated_idx={f.mitigated_idx}")

    bullish = [f for f in fvgs if f.kind == FVGKind.BULLISH]
    check("genau 1 Bullish-FVG um die Impuls-Kerze",
          len(bullish) == 1,
          f"got {len(bullish)}")
    if bullish:
        check("FVG-Low == 100.20 (High der Bar mid-1)",
              abs(bullish[0].low - 100.20) < 0.01,
              f"got {bullish[0].low}")
        check("FVG-High == 100.60 (Low der Bar mid+1)",
              abs(bullish[0].high - 100.60) < 0.01,
              f"got {bullish[0].high}")


def test_no_lookahead() -> None:
    """Wichtig: Detektoren duerfen zu einem gegebenen Index i nur
    Daten <= i verwenden. Wir testen das, indem wir dieselbe Analyse
    auf einem Prefix laufen lassen und pruefen, dass die erkannten
    Events auf dem Prefix auch im Full-Run existieren (gleicher idx)."""
    print("\n--- TEST 3: No-Lookahead ---")
    df = make_bullish_then_reversal(n=400, seed=7)
    prefix_n = 250
    df_prefix = df.iloc[:prefix_n]
    snap_full = analyze(df)
    snap_prefix = analyze(df_prefix)

    # Events aus Prefix muessen als Teilmenge im Full-Run vorkommen
    # (gleicher idx + gleicher kind). Wir tolerieren, dass ein Event
    # am Prefix-Rand im Full-Run spaeter bestaetigt wird.
    full_keys = {(e.idx, e.kind) for e in snap_full.events
                 if e.idx < prefix_n - config.SWING_LOOKBACK}
    prefix_keys = {(e.idx, e.kind) for e in snap_prefix.events
                   if e.idx < prefix_n - config.SWING_LOOKBACK}
    missing = prefix_keys - full_keys
    extra = full_keys - prefix_keys
    check("Events im Prefix sind Teilmenge der Full-Events",
          len(missing) == 0,
          f"missing={missing}")
    # Extra ist ok, im Full-Run koennen spaetere Reversals aeltere
    # Events neu klassifizieren - wir geben es nur als Info aus
    if extra:
        print(f"    [info] full_run kennt {len(extra)} zusaetzliche Events "
              f"in der Prefix-Region (ok, spaetere Kontextaenderung)")


def test_pd_directions() -> None:
    print("\n--- TEST 4: Premium/Discount Zonen ---")
    df = make_bullish_then_reversal(n=300, seed=3)
    snap = analyze(df)
    pdz = current_pd_zone(snap.swings)
    check("PD-Zone existiert", pdz is not None)
    if pdz:
        dlow, dhigh = pdz.zone_for(+1)
        plow, phigh = pdz.zone_for(-1)
        check("Discount-Zone ist untere Haelfte",
              dlow == pdz.low and dhigh == pdz.equilibrium)
        check("Premium-Zone ist obere Haelfte",
              plow == pdz.equilibrium and phigh == pdz.high)


def main() -> None:
    print("=" * 78)
    print("  SMC SMOKETEST  (synthetische Daten, offline)")
    print("=" * 78)
    test_structure()
    test_fvg()
    test_no_lookahead()
    test_pd_directions()

    print()
    print("=" * 78)
    print("  ERGEBNIS: " + ("PASSED" if checks_passed else "FAILED"))
    print("=" * 78)
    import sys
    sys.exit(0 if checks_passed else 1)


if __name__ == "__main__":
    main()
