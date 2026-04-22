"""
smc_demo.py  (forex_bot_smc)
============================

Demo + Visualisierung der SMC-Detektoren.

Usage:
    # mit yfinance (60 Tage M15, schnell fuer Smoketest)
    python3 smc_demo.py --symbol US500 --tf M15

    # mit eigener CSV (MT5/Dukascopy)
    python3 smc_demo.py --symbol EURUSD --tf M15 --source csv

    # ohne Plot (nur Zahlen)
    python3 smc_demo.py --symbol XAUUSD --tf H1 --no-plot

Plot speichert in results/smc_{symbol}_{tf}.png.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np
import pandas as pd

import config
from data_loader import load_symbol
from smc_structure import (
    SwingType, EventType, current_pd_zone, find_swings,
)
from smc_patterns import (
    analyze, OBKind, FVGKind, SweepKind,
    unmitigated_obs, unmitigated_fvgs,
)


def print_summary(symbol: str, tf: str, snap) -> None:
    s = snap.summary()
    bar1 = snap.df.index[0]
    bar_last = snap.df.index[-1]
    print("=" * 78)
    print(f"  SMC ANALYSE  {symbol} / {tf}   "
          f"{bar1}  ->  {bar_last}")
    print("=" * 78)
    print(f"  Bars                : {s['bars']:>6}")
    print(f"  Swings              : {s['swings']:>6} "
          f"(High/Low je ~{s['swings']//2})")
    print(f"  BOS up / down       : {s['bos_up']:>6}  / {s['bos_down']:>6}")
    print(f"  CHoCH up / down     : {s['choch_up']:>6}  / {s['choch_down']:>6}")
    print(f"  Order Blocks bull.  : {s['ob_bullish']:>6}")
    print(f"  Order Blocks bear.  : {s['ob_bearish']:>6}")
    print(f"  FVG bullish / bear. : {s['fvg_bullish']:>6}  / {s['fvg_bearish']:>6}")
    print(f"  Liq Sweeps buy/sell : {s['sweeps_buy']:>6}  / {s['sweeps_sell']:>6}")

    # Aktuelle Situation
    cur_idx = len(snap.df) - 1
    active_obs = unmitigated_obs(snap.order_blocks, cur_idx)
    active_fvgs = unmitigated_fvgs(snap.fvgs, cur_idx)
    pdz = current_pd_zone(snap.swings)
    last_close = snap.df["Close"].iloc[-1]
    print("-" * 78)
    print(f"  AKTUELL (Close={last_close:.5f}):")
    print(f"    Aktive (unmitigated) OBs : {len(active_obs)}")
    print(f"    Aktive (unmitigated) FVGs: {len(active_fvgs)}")
    if pdz:
        in_discount = last_close < pdz.equilibrium
        zone = "DISCOUNT" if in_discount else "PREMIUM"
        print(f"    Swing-Range: {pdz.low:.5f} .. {pdz.high:.5f}  "
              f"(EQ={pdz.equilibrium:.5f})  -> Close im {zone}")
    if snap.events:
        last_ev = snap.events[-1]
        print(f"    Letzter Struktur-Event: {last_ev.kind.value} "
              f"@ {last_ev.timestamp} (Break {last_ev.break_price:.5f})")


def make_plot(symbol: str, tf: str, snap, out_path: Path,
              window: int = 200) -> None:
    """Candlestick-Plot + Swings + Events + aktive OBs/FVGs.

    X-Achse ist int-indiziert (0..N-1), damit Wochenend-/Holiday-Gaps
    visuell verschwinden. Datum-Labels werden bei passenden Ticks
    nachtraeglich gesetzt.
    """
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except ImportError:
        print("[WARN] matplotlib nicht installiert, ueberspringe Plot.")
        return

    df = snap.df
    if len(df) < 10:
        print("[WARN] zu wenige Bars fuer Plot.")
        return

    # Window auf letzte N Bars beschraenken
    if len(df) > window:
        win = df.iloc[-window:]
        win_start_idx = len(df) - window
    else:
        win = df
        win_start_idx = 0
    win_end_idx = win_start_idx + len(win) - 1

    fig, ax = plt.subplots(figsize=(14, 7))

    # Wir plotten alles gegen integer x-Positionen (relativ zum Window).
    # -> Wochenend-Gaps verschwinden.
    def xpos(idx_abs: int) -> float:
        """Absoluter DF-Index -> x-Position im Plot (0..len(win)-1)."""
        return idx_abs - win_start_idx

    # ---------- CANDLESTICKS ----------
    # Tuned fuer M15/H1: schlanker Docht, breiter Body.
    body_w = 0.7
    opens = win["Open"].to_numpy()
    highs = win["High"].to_numpy()
    lows = win["Low"].to_numpy()
    closes = win["Close"].to_numpy()

    for i in range(len(win)):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        up = c >= o
        color = "#26a69a" if up else "#ef5350"        # green / red
        # Docht
        ax.plot([i, i], [l, h], color=color, linewidth=0.8, zorder=2)
        # Body
        lower = min(o, c); height = max(abs(c - o), 1e-9)
        ax.add_patch(Rectangle(
            (i - body_w / 2, lower), body_w, height,
            facecolor=color, edgecolor=color, linewidth=0.4,
            zorder=3,
        ))

    # ---------- SWINGS ----------
    for s in snap.swings:
        if s.idx < win_start_idx or s.idx > win_end_idx:
            continue
        x = xpos(s.idx)
        if s.kind == SwingType.LOW:
            ax.scatter(x, s.price, marker="^", s=36, c="#2e7d32",
                       edgecolor="black", linewidths=0.3, zorder=6)
        else:
            ax.scatter(x, s.price, marker="v", s=36, c="#c62828",
                       edgecolor="black", linewidths=0.3, zorder=6)

    # ---------- EVENTS (Break-Linien + Labels) ----------
    ev_colors = {
        EventType.BOS_UP: "#1b5e20",      # dunkelgruen
        EventType.BOS_DOWN: "#b71c1c",    # dunkelrot
        EventType.CHOCH_UP: "#43a047",    # hellgruen
        EventType.CHOCH_DOWN: "#fb8c00",  # orange
    }
    for ev in snap.events:
        if ev.idx < win_start_idx or ev.idx > win_end_idx:
            continue
        x = xpos(ev.idx)
        color = ev_colors[ev.kind]
        # Break-Line nur ab Trigger-Swing bis Event (nicht ueber ganze Chart)
        x_start = max(xpos(ev.trigger_swing.idx), 0)
        ax.hlines(ev.break_price, x_start, x, colors=color,
                  linewidth=0.7, alpha=0.55, zorder=4)
        ax.annotate(ev.kind.value, xy=(x, ev.break_price),
                    xytext=(2, 6), textcoords="offset points",
                    fontsize=7, color=color, rotation=20, zorder=7)

    # ---------- AKTIVE OBs (unmitigated) ----------
    cur_idx = len(df) - 1
    for ob in unmitigated_obs(snap.order_blocks, cur_idx):
        if ob.idx > win_end_idx:
            continue
        x0 = max(xpos(ob.idx), 0)
        x1 = xpos(win_end_idx) + 1
        color = "#2e7d32" if ob.kind == OBKind.BULLISH else "#c62828"
        ax.add_patch(Rectangle(
            (x0, ob.low), x1 - x0, ob.high - ob.low,
            alpha=0.22, facecolor=color, edgecolor=color,
            linewidth=0.8, zorder=1,
        ))

    # ---------- AKTIVE FVGs (unmitigated) ----------
    for fvg in unmitigated_fvgs(snap.fvgs, cur_idx):
        if fvg.idx > win_end_idx:
            continue
        x0 = max(xpos(fvg.idx), 0)
        x1 = xpos(win_end_idx) + 1
        color = "#1565c0" if fvg.kind == FVGKind.BULLISH else "#6a1b9a"
        ax.add_patch(Rectangle(
            (x0, fvg.low), x1 - x0, fvg.high - fvg.low,
            alpha=0.14, facecolor=color, edgecolor=color,
            linewidth=0.5, hatch="///", zorder=1,
        ))

    # ---------- SWEEPS ----------
    for sw in snap.sweeps:
        if sw.idx < win_start_idx or sw.idx > win_end_idx:
            continue
        x = xpos(sw.idx)
        color = "#c62828" if sw.kind == SweepKind.BUY_SIDE else "#2e7d32"
        ax.scatter(x, sw.wick_extreme, marker="x", s=90, c=color,
                   linewidths=2.0, zorder=8)

    # ---------- X-ACHSEN-LABELS (Datum an ausgewaehlten Ticks) ----------
    n_ticks = min(10, len(win))
    tick_positions = np.linspace(0, len(win) - 1, n_ticks).astype(int)
    tick_labels = [win.index[p].strftime("%m-%d %H:%M")
                   for p in tick_positions]
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels, rotation=30, ha="right")
    ax.set_xlim(-0.5, len(win) - 0.5)

    ax.set_title(f"SMC  {symbol}  {tf}   [window: last {len(win)} bars]")
    ax.set_ylabel("Preis")
    ax.grid(True, alpha=0.25, zorder=0)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)
    print(f"  Plot gespeichert: {out_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="SMC/ICT Demo + Visualisierung")
    ap.add_argument("--symbol", default="US500",
                    help="Symbol aus config.INSTRUMENTS (default: US500)")
    ap.add_argument("--tf", default="M15",
                    help="Timeframe (M15, H1, ...)")
    ap.add_argument("--source", default="auto",
                    choices=["auto", "csv", "yfinance"])
    ap.add_argument("--window", type=int, default=200,
                    help="Letzte N Bars im Plot (default 200)")
    ap.add_argument("--no-plot", action="store_true")
    args = ap.parse_args()

    print(f"Lade {args.symbol} {args.tf} (source={args.source}) ...")
    df = load_symbol(args.symbol, timeframes=[args.tf], source=args.source)[args.tf]
    print(f"  -> {len(df)} Bars")

    print("Analysiere SMC-Struktur ...")
    snap = analyze(df)
    print_summary(args.symbol, args.tf, snap)

    if not args.no_plot:
        out = Path(config.RESULTS_DIR) / f"smc_{args.symbol}_{args.tf}.png"
        make_plot(args.symbol, args.tf, snap, out, window=args.window)


if __name__ == "__main__":
    main()
