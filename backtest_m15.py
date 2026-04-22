"""
backtest_m15.py  (forex_bot_smc)
================================

Event-driven M15-Backtester fuer SMC-Setups aus smc_strategy.py.

Mechanik
--------
- Eine aktive Position zur Zeit (kein Pyramiding).
- TP1 = 2R (50% Close), TP2 = 4R (25% Close), Runner = 25%.
- Nach TP1: SL auf Entry (Break-Even) geshiftet.
- Max-Hold 48 Bars (=12h M15) -> Rest bei Bar-Close.
- 3 Losses in einem Tag -> kein weiterer Trade heute.
- FTMO-Monitoring: 5% Daily, 10% Total Drawdown.
- SL-First-Regel: falls eine Bar sowohl SL als auch TP enthaelt, wird SL
  zuerst getriggert (pessimistisch).

Risk-Model
----------
Fix 1% Risk pro Trade = 1R. PnL in R-Einheiten berechnet und fuer die
Equity-Kurve in Dollar umgerechnet (1R = RISK_PCT * ACCOUNT_START).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd
import config

from data_loader import load_symbol, resample
from smc_patterns import analyze
from smc_strategy import find_all_setups, Setup


# ---------------------------------------------------------------------------
# Parameter
# ---------------------------------------------------------------------------
ACCOUNT_START       = 10_000.0
RISK_PCT            = config.RISK_PER_TRADE   # aus config
MAX_HOLD_BARS       = 48             # 12h auf M15
TP1_CLOSE_FRAC      = 0.50
TP2_CLOSE_FRAC      = 0.25
MAX_LOSSES_PER_DAY  = 3
FTMO_DAILY_LOSS     = 0.05           # 5% Daily
FTMO_TOTAL_LOSS     = 0.10           # 10% Total


# ---------------------------------------------------------------------------
# Datentypen
# ---------------------------------------------------------------------------
@dataclass
class OpenTrade:
    setup: Setup
    entry_bar: int
    entry_price: float
    initial_sl: float
    current_sl: float
    tp1_price: float
    tp2_price: float
    direction: str
    tp1_hit: bool = False
    tp2_hit: bool = False
    remaining_frac: float = 1.0
    r_accumulated: float = 0.0
    exits: List[Tuple[int, float, float, str]] = field(default_factory=list)
    # (bar_idx, price, fraction_closed, reason)

    def r_from_exit(self, exit_price: float) -> float:
        """R-Multiple relativ zum initialen SL-Risiko."""
        if self.direction == "long":
            risk = self.entry_price - self.initial_sl
            return (exit_price - self.entry_price) / risk
        else:
            risk = self.initial_sl - self.entry_price
            return (self.entry_price - exit_price) / risk


@dataclass
class ClosedTrade:
    setup: Setup
    entry_bar: int
    entry_time: pd.Timestamp
    entry_price: float
    initial_sl: float
    tp1_price: float
    tp2_price: float
    exits: List[Tuple[int, float, float, str]]
    total_r: float
    bars_held: int

    def summary_row(self) -> dict:
        return {
            "entry_time":  self.entry_time,
            "direction":   self.setup.direction,
            "zone_kind":   self.setup.zone_kind,
            "entry":       round(self.entry_price, 5),
            "sl":          round(self.initial_sl, 5),
            "tp1":         round(self.tp1_price, 5),
            "tp2":         round(self.tp2_price, 5),
            "exits":       "; ".join(
                f"{reason}@{p:.5f}×{frac:.2f}" for _, p, frac, reason in self.exits),
            "total_r":     round(self.total_r, 3),
            "bars_held":   self.bars_held,
        }


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
def simulate(ltf_df: pd.DataFrame,
             setups: List[Setup]) -> Tuple[List[ClosedTrade], pd.Series, dict]:

    setups_by_idx = {s.entry_idx: s for s in setups}

    closed: List[ClosedTrade] = []
    active: Optional[OpenTrade] = None

    equity = ACCOUNT_START
    equity_curve: List[Tuple[pd.Timestamp, float]] = []

    day = None
    day_start_equity = ACCOUNT_START
    losses_today = 0

    ftmo_fail = False
    ftmo_reason: Optional[str] = None

    n = len(ltf_df)
    opens = ltf_df["Open"].to_numpy()
    highs = ltf_df["High"].to_numpy()
    lows  = ltf_df["Low"].to_numpy()
    closes = ltf_df["Close"].to_numpy()
    index = ltf_df.index

    for i in range(n):
        bar_time = index[i]
        bar_date = bar_time.date() if hasattr(bar_time, "date") else bar_time

        # Day rollover
        if day is None:
            day = bar_date
            day_start_equity = equity
        elif bar_date != day:
            day = bar_date
            day_start_equity = equity
            losses_today = 0

        o, h, l, c = opens[i], highs[i], lows[i], closes[i]

        # ------- Manage active trade -------
        if active is not None:
            # 1. SL first (pessimistisch)
            sl_hit = ((active.direction == "long"  and l <= active.current_sl) or
                      (active.direction == "short" and h >= active.current_sl))
            if sl_hit:
                reason = "BE" if (active.tp1_hit and
                                  abs(active.current_sl - active.entry_price) < 1e-9) \
                              else "SL"
                r = active.r_from_exit(active.current_sl)
                active.exits.append((i, active.current_sl, active.remaining_frac, reason))
                active.r_accumulated += r * active.remaining_frac
                active.remaining_frac = 0.0

            # 2. TP1
            if active.remaining_frac > 0 and not active.tp1_hit:
                tp1_hit = ((active.direction == "long"  and h >= active.tp1_price) or
                           (active.direction == "short" and l <= active.tp1_price))
                if tp1_hit:
                    r = active.r_from_exit(active.tp1_price)
                    active.exits.append((i, active.tp1_price, TP1_CLOSE_FRAC, "TP1"))
                    active.r_accumulated += r * TP1_CLOSE_FRAC
                    active.remaining_frac -= TP1_CLOSE_FRAC
                    active.tp1_hit = True
                    active.current_sl = active.entry_price  # BE-Shift

            # 3. TP2
            if active.remaining_frac > 0 and active.tp1_hit and not active.tp2_hit:
                tp2_hit = ((active.direction == "long"  and h >= active.tp2_price) or
                           (active.direction == "short" and l <= active.tp2_price))
                if tp2_hit:
                    r = active.r_from_exit(active.tp2_price)
                    active.exits.append((i, active.tp2_price, TP2_CLOSE_FRAC, "TP2"))
                    active.r_accumulated += r * TP2_CLOSE_FRAC
                    active.remaining_frac -= TP2_CLOSE_FRAC
                    active.tp2_hit = True

            # 4. Max-Hold
            if active.remaining_frac > 0 and (i - active.entry_bar) >= MAX_HOLD_BARS:
                r = active.r_from_exit(c)
                active.exits.append((i, c, active.remaining_frac, "HOLD"))
                active.r_accumulated += r * active.remaining_frac
                active.remaining_frac = 0.0

            # Trade komplett geschlossen?
            if active.remaining_frac <= 1e-9:
                ct = ClosedTrade(
                    setup=active.setup,
                    entry_bar=active.entry_bar,
                    entry_time=index[active.entry_bar],
                    entry_price=active.entry_price,
                    initial_sl=active.initial_sl,
                    tp1_price=active.tp1_price,
                    tp2_price=active.tp2_price,
                    exits=active.exits,
                    total_r=active.r_accumulated,
                    bars_held=i - active.entry_bar,
                )
                closed.append(ct)
                equity += ct.total_r * ACCOUNT_START * RISK_PCT
                if ct.total_r < -1e-9:
                    losses_today += 1
                active = None

        # ------- Open new trade? -------
        if active is None and not ftmo_fail and i in setups_by_idx:
            if losses_today < MAX_LOSSES_PER_DAY:
                s = setups_by_idx[i]
                active = OpenTrade(
                    setup=s,
                    entry_bar=i,
                    entry_price=s.entry_price,
                    initial_sl=s.sl,
                    current_sl=s.sl,
                    tp1_price=s.tp1,
                    tp2_price=s.tp2,
                    direction=s.direction,
                )

        # ------- FTMO checks -------
        if not ftmo_fail:
            daily_loss = (day_start_equity - equity) / day_start_equity \
                         if day_start_equity > 0 else 0
            total_loss = (ACCOUNT_START - equity) / ACCOUNT_START
            if daily_loss >= FTMO_DAILY_LOSS:
                ftmo_fail = True
                ftmo_reason = f"Daily loss {daily_loss:.2%} @ {bar_time}"
            elif total_loss >= FTMO_TOTAL_LOSS:
                ftmo_fail = True
                ftmo_reason = f"Total loss {total_loss:.2%} @ {bar_time}"

        equity_curve.append((bar_time, equity))

    # Am Ende noch offene Position schliessen
    if active is not None:
        last_c = closes[-1]
        r = active.r_from_exit(last_c)
        active.exits.append((n - 1, last_c, active.remaining_frac, "EOD"))
        active.r_accumulated += r * active.remaining_frac
        ct = ClosedTrade(
            setup=active.setup,
            entry_bar=active.entry_bar,
            entry_time=index[active.entry_bar],
            entry_price=active.entry_price,
            initial_sl=active.initial_sl,
            tp1_price=active.tp1_price,
            tp2_price=active.tp2_price,
            exits=active.exits,
            total_r=active.r_accumulated,
            bars_held=n - 1 - active.entry_bar,
        )
        closed.append(ct)
        equity += ct.total_r * ACCOUNT_START * RISK_PCT

    eq = pd.Series([e[1] for e in equity_curve],
                   index=[e[0] for e in equity_curve], name="equity")

    meta = {
        "ftmo_fail": ftmo_fail,
        "ftmo_reason": ftmo_reason,
        "final_equity": equity,
    }
    return closed, eq, meta


# ---------------------------------------------------------------------------
# Metriken
# ---------------------------------------------------------------------------
def metrics(trades: List[ClosedTrade], eq: pd.Series, meta: dict) -> dict:
    n = len(trades)
    if n == 0:
        return {"n_trades": 0, **meta}

    rs = [t.total_r for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    running_max = eq.cummax()
    dd = (eq - running_max) / running_max
    max_dd_pct = float(dd.min() * 100) if len(dd) else 0.0
    final_equity = float(eq.iloc[-1]) if len(eq) else ACCOUNT_START

    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    profit_factor = (sum(wins) / abs(sum(losses))) if losses else float("inf")

    return {
        "n_trades":         n,
        "wins":             len(wins),
        "losses":           len(losses),
        "win_rate_pct":     len(wins) / n * 100,
        "avg_r":            sum(rs) / n,
        "avg_win_r":        avg_win,
        "avg_loss_r":       avg_loss,
        "profit_factor":    profit_factor,
        "sum_r":            sum(rs),
        "best_r":           max(rs),
        "worst_r":          min(rs),
        "final_equity":     final_equity,
        "total_return_pct": (final_equity - ACCOUNT_START) / ACCOUNT_START * 100,
        "max_dd_pct":       max_dd_pct,
        "ftmo_fail":        meta["ftmo_fail"],
        "ftmo_reason":      meta["ftmo_reason"],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="SMC M15 Backtest")
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="M15")
    ap.add_argument("--htf", default="H1")
    ap.add_argument("--source", default="csv",
                    choices=["auto", "csv", "yfinance"])
    ap.add_argument("--limit", type=int, default=5000,
                    help="Letzte N LTF-Bars (Default 5000). 0 = kompletter "
                         "Datensatz ohne Abschneiden.")
    ap.add_argument("--trades-out", default="results/bt_trades.csv")
    ap.add_argument("--equity-out", default="results/bt_equity.png")
    ap.add_argument("--direction", default="auto",
                    choices=["auto", "shorts-only", "longs-only", "both"],
                    help="Override TRADE_LONGS/TRADE_SHORTS fuer diesen Run. "
                         "'auto' = nutze config.py Defaults.")
    ap.add_argument("--no-d1bias", action="store_true",
                    help="Disable D1_BIAS_FILTER fuer diesen Run.")
    ap.add_argument("--no-vola", action="store_true",
                    help="Disable VOLA_REGIME_FILTER fuer diesen Run.")
    ap.add_argument("--no-session", action="store_true",
                    help="Disable SESSION_FILTER fuer diesen Run.")
    ap.add_argument("--no-ct-adx", action="store_true",
                    help="Disable CT_ADX_FILTER fuer diesen Run.")
    args = ap.parse_args()

    # Direction-Override: patche config-Modul VOR Strategy-Aufrufen.
    # smc_strategy.py liest _cfg.TRADE_LONGS/_SHORTS zur Laufzeit.
    if args.direction != "auto":
        import config as _cfg_patch
        if args.direction == "shorts-only":
            _cfg_patch.TRADE_LONGS = False
            _cfg_patch.TRADE_SHORTS = True
        elif args.direction == "longs-only":
            _cfg_patch.TRADE_LONGS = True
            _cfg_patch.TRADE_SHORTS = False
        elif args.direction == "both":
            _cfg_patch.TRADE_LONGS = True
            _cfg_patch.TRADE_SHORTS = True
        print(f"[DIRECTION-OVERRIDE] {args.direction}: "
              f"TRADE_LONGS={_cfg_patch.TRADE_LONGS}, "
              f"TRADE_SHORTS={_cfg_patch.TRADE_SHORTS}")
        
    # Filter-Override: patche config-Modul, bevor Strategy aufgerufen wird.
    if args.no_d1bias or args.no_vola or args.no_session or args.no_ct_adx:
        import config as _cfg_filter
        if args.no_d1bias:
            _cfg_filter.D1_BIAS_FILTER_ENABLED = False
        if args.no_vola:
            _cfg_filter.VOLA_REGIME_FILTER_ENABLED = False
        if args.no_session:
            _cfg_filter.SESSION_FILTER_ENABLED = False
        if args.no_ct_adx:
            _cfg_filter.CT_ADX_FILTER_ENABLED = False
        print(f"[FILTER-OVERRIDE] "
              f"D1_BIAS={getattr(_cfg_filter, 'D1_BIAS_FILTER_ENABLED', True)}, "
              f"VOLA_REGIME={getattr(_cfg_filter, 'VOLA_REGIME_FILTER_ENABLED', True)}, "
              f"SESSION={getattr(_cfg_filter, 'SESSION_FILTER_ENABLED', True)}, "
              f"CT_ADX={getattr(_cfg_filter, 'CT_ADX_FILTER_ENABLED', True)}")
        
    # Per-Symbol CT/ADX-Auto-Exclude
    # Fuer Symbole in CT_ADX_FILTER_EXCLUDE_SYMBOLS wird der Filter
    # automatisch deaktiviert (siehe config.py Kommentar).
    import config as _cfg_excl
    _exclude = getattr(_cfg_excl, "CT_ADX_FILTER_EXCLUDE_SYMBOLS", [])
    if (args.symbol in _exclude
            and getattr(_cfg_excl, "CT_ADX_FILTER_ENABLED", False)):
        _cfg_excl.CT_ADX_FILTER_ENABLED = False
        print(f"[CT_ADX-EXCLUDE] {args.symbol} ist in "
              f"CT_ADX_FILTER_EXCLUDE_SYMBOLS -> Filter AUS fuer diesen Run")

    print(f"Lade {args.symbol} {args.tf} (source={args.source}) ...")
    ltf_df = load_symbol(args.symbol, timeframes=[args.tf],
                         source=args.source)[args.tf]
    if args.limit and args.limit > 0:
        ltf_df = ltf_df.iloc[-args.limit:]
    print(f"  -> {len(ltf_df)} Bars ({ltf_df.index[0]} -> {ltf_df.index[-1]})")

    print(f"Resample nach {args.htf} ...")
    htf_df = resample(ltf_df, args.htf)
    print(f"  -> {len(htf_df)} {args.htf}-Bars")

    print("Analysiere ...")
    htf_snap = analyze(htf_df)
    ltf_snap = analyze(ltf_df)

    print("Setup-Finder ...")
    setups = find_all_setups(ltf_df, ltf_snap, htf_snap, htf_df=htf_df)
    print(f"  -> {len(setups)} Setups")

    print("Simuliere ...")
    trades, eq, meta = simulate(ltf_df, setups)

    m = metrics(trades, eq, meta)

    print("=" * 78)
    print(f"  BACKTEST  {args.symbol} {args.tf}  "
          f"({len(ltf_df)} Bars, {len(setups)} Setups)")
    print("=" * 78)
    for k in ("n_trades", "wins", "losses", "win_rate_pct",
              "avg_r", "avg_win_r", "avg_loss_r", "profit_factor",
              "sum_r", "best_r", "worst_r",
              "final_equity", "total_return_pct", "max_dd_pct",
              "ftmo_fail", "ftmo_reason"):
        v = m.get(k)
        if isinstance(v, float):
            print(f"  {k:20}: {v:>12.4f}")
        else:
            print(f"  {k:20}: {v}")

    # CSV speichern
    if trades:
        df_trades = pd.DataFrame([t.summary_row() for t in trades])
        out_csv = Path(args.trades_out)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        df_trades.to_csv(out_csv, index=False)
        print(f"\n  Trades:  {out_csv}")

    # Equity-Plot
    try:
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(12, 6))
        eq.plot(ax=ax, color="#1976d2", linewidth=1.2)
        ax.axhline(ACCOUNT_START, color="gray", linestyle="--", alpha=0.6)
        ax.axhline(ACCOUNT_START * (1 - FTMO_TOTAL_LOSS),
                   color="#c62828", linestyle=":", alpha=0.6,
                   label="FTMO -10%")
        ax.axhline(ACCOUNT_START * 1.08, color="#2e7d32",
                   linestyle=":", alpha=0.6, label="FTMO +8% Target")
        ax.set_title(f"Equity Curve  {args.symbol} {args.tf}  "
                     f"({len(trades)} Trades)")
        ax.set_ylabel("Equity ($)")
        ax.legend(loc="best")
        ax.grid(True, alpha=0.25)
        out_png = Path(args.equity_out)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        fig.tight_layout()
        fig.savefig(out_png, dpi=140)
        plt.close(fig)
        print(f"  Equity:  {out_png}")
    except ImportError:
        print("  [WARN] matplotlib fehlt -> kein Equity-Plot")