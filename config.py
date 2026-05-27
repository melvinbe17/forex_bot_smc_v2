"""
config.py  (forex_bot_smc)
==========================

Zentrale Konfiguration fuer den SMC/ICT-Bot (M15).

Strategie-Grundlage: Smart Money Concepts / Inner Circle Trader.
  HTF-Bias (H4/H1) -> LTF-Setup (M15) -> Entry-Confirmation (M1/M5)

Dieser Bot handelt NICHT auf Daily-Trendfolge wie forex_bot_v3, sondern:
  1) identifiziert Markt-Struktur (BOS, CHoCH, Swings)
  2) markiert Liquiditaet, Order Blocks, Fair Value Gaps
  3) handelt Pullbacks in Premium/Discount-Zonen mit HTF-Bias
"""

from __future__ import annotations

# ----------------------------------------------------------------------
# KONTO / FTMO-RULES
# ----------------------------------------------------------------------
ACCOUNT_SIZE_USD = 10_000.0
RISK_PER_TRADE = 0.0075              # 0.75% pro Trade (SMC ist praeziser,
                                     # deshalb niedriger als v3)
MAX_CONCURRENT_POSITIONS = 2         # M15-Setups sind enger korreliert
MAX_LOSSES_PER_DAY = 3               # nach 3 SLs -> Bot stoppt fuer heute
                                     # (Regel aus dem SMC-Checklist-Bild)
FTMO_DAILY_LOSS_LIMIT = 0.05
FTMO_MAX_LOSS_LIMIT = 0.10

# ----------------------------------------------------------------------
# TIMEFRAMES  (Multi-TF-Ansatz)
# ----------------------------------------------------------------------
HTF_TIMEFRAME = "H1"                 # Bias / Trendrichtung
LTF_TIMEFRAME = "M15"                # Setup-Timeframe (OB/FVG/Liq)
ENTRY_TIMEFRAME = "M1"               # Entry-Confirmation (M1-BOS)

# ----------------------------------------------------------------------
# STRUCTURE DETECTION
# ----------------------------------------------------------------------
# Fractal-basierte Swing-Detection: high[i] > max(high[i-N..i-1]) AND
# high[i] > max(high[i+1..i+N]).
# Tuned v2 (21.04.2026): von 2 auf 3 erhoeht (= 7-Bar-Fractal), weil
# 5-Bar-Fractal auf M15 zu viele Mini-Zigzags als Swings markiert.
SWING_LOOKBACK = 3                   # 2 = 5-Bar-Swing, 3 = 7-Bar-Swing
MIN_SWING_DISTANCE_ATR = 1.0         # Swings unter 1.0*ATR ignorieren
                                     # (Noise-Zigzags rausfiltern)

# BOS/CHoCH: Breakout-Bestaetigung per Close (nicht nur Wick)
BOS_CONFIRMATION = "close"           # "close" oder "wick"

# Anti-Dopplung: gleicher Event-Typ innerhalb N Bars mit Break-Price
# der <= TOL*ATR weg liegt wird unterdrueckt (verhindert Label-Spam).
EVENT_DEDUP_BARS = 10
EVENT_DEDUP_PRICE_TOL_ATR = 0.3

# ----------------------------------------------------------------------
# ORDER BLOCKS  (Last opposing candle before BOS impulse)
# ----------------------------------------------------------------------
OB_LOOKBACK_BARS = 5                 # engere OB-Suche (war 10) - vermeidet
                                     # OBs die zu weit vom Impuls entfernt
                                     # sind und selten sauber mitigaten
OB_MAX_AGE_BARS = 50                 # OBs aelter als das verfallen
OB_MITIGATION = "touch"              # "touch" = Wick beruehrt -> gueltig
                                     # "fifty" = 50% des OB muss beruehrt
                                     #           werden

# ----------------------------------------------------------------------
# FAIR VALUE GAPS  (3-Candle-Imbalance)
# ----------------------------------------------------------------------
FVG_MIN_SIZE_ATR = 0.35              # FVG muss min 0.35*ATR gross sein
                                     # (Noise-Mini-Gaps ausfiltern)
FVG_MAX_AGE_BARS = 50                # alte FVGs werden verworfen

# ----------------------------------------------------------------------
# LIQUIDITY SWEEP
# ----------------------------------------------------------------------
# Sweep = Wick bricht Swing-High/Low, Close kommt wieder zurueck.
# Tuned: nur echte Grabs (min 0.6*ATR Wick) statt jedes kleine Ueberschiessen.
SWEEP_MIN_WICK_ATR = 0.6             # war 0.3
SWEEP_MAX_CLOSE_BACK = 0.5           # Close muss zu >=50% zurueck

# ----------------------------------------------------------------------
# PREMIUM / DISCOUNT  (50%-Fib des aktuellen Swing-Range)
# ----------------------------------------------------------------------
# Bullish: Long nur im DISCOUNT-Bereich (unterhalb 50%).
# Bearish: Short nur im PREMIUM-Bereich (oberhalb 50%).
PD_EQUILIBRIUM = 0.50                # 50% = exakt in der Mitte

# ----------------------------------------------------------------------
# TRADE MANAGEMENT
# ----------------------------------------------------------------------
SL_BUFFER_ATR = 0.2                  # SL = Swing +/- 0.2*ATR Puffer
TP1_RR = 2.0                         # Partial 1 bei 1:2
TP1_PERCENT = 0.50                   # 50% schliessen
TP2_RR = 4.0                         # Partial 2 bei 1:4
TP2_PERCENT = 0.25                   # weitere 25% (25% Runner)
BE_AFTER_M1_BOS = True               # SL auf Break-Even nach M1-BOS
MAX_HOLD_BARS_M15 = 48               # 48 * M15 = 12h max Hold

# ----------------------------------------------------------------------
# ZONE FILTER  (welche Setup-Zonen zulassen)
# ----------------------------------------------------------------------
# 5-Jahres-Breakdown (2021-2026 EURUSD M15, 1480 Trades):
#   FVG-Setups: 1304 Trades, +54.45R, PF 1.08
#   OB-Setups :  176 Trades,  -4.01R, PF 0.96   <- leicht negativ
# Der OB-Detector markiert die LETZTE gegenlaeufige Kerze vor dem Impuls.
# Auf M15 findet er zu wenige und die Stichprobe ist durchwachsen.
# Default: nur FVG-Setups. Wenn der OB-Detector irgendwann verbessert
# ist (VOR-Impuls-Displacement-OB), kann man ZONE_USE_OB wieder aktivieren.
ZONE_USE_OB = False
ZONE_USE_FVG = True

# ----------------------------------------------------------------------
# DIRECTION FILTER  (Long/Short einzeln abschaltbar)
# ----------------------------------------------------------------------
# 5-Jahres-Breakdown (EURUSD M15, v7):
#   Long : 152 Trades, sumR  +2.85R, PF 1.03, DD_R -14.48
#   Short: 158 Trades, sumR +47.54R, PF 1.58, DD_R -12.99
# Longs sind praktisch flat und produzieren DD ohne Alpha. Deaktivieren
# -> cleaner Edge, ~halber DD bei minimalem R-Verlust. Shorts-only ist
# fuer EURUSD intuitiv sinnvoll (USD-Bias in Bear-Jahren 2021/22/25).

# Default: Short-Only (safe Default nach 5J-Test auf EURUSD).
# Per-Symbol-Override via CLI-Flag --direction in backtest_m15.py:
#   --direction auto          -> nutze diese Defaults
#   --direction shorts-only   -> TRADE_LONGS=False, TRADE_SHORTS=True
#   --direction longs-only    -> TRADE_LONGS=True,  TRADE_SHORTS=False
#   --direction both          -> TRADE_LONGS=True,  TRADE_SHORTS=True
TRADE_LONGS = False
TRADE_SHORTS = True

# ----------------------------------------------------------------------
# VOLATILITY-REGIME-FILTER
# ----------------------------------------------------------------------
# Die Verluste im 5-Jahres-Backtest clustern in Vola-Expansion-Phasen
# (2022: Crash 1.15->0.96, -54R; 2024Q4: USD-Rally, -21R). Unsere Stops
# (Swing +/- 0.2*ATR) und TPs (2R/4R) sind fuer normale Vola kalibriert -
# wenn die Vola sich verdoppelt, werden SLs overshoot bevor TP1 erreicht
# wird und die Win-Rate kollabiert.
#
# Filter: H1-ATR(14) > VOLA_REGIME_MAX_RATIO * rolling-Median(200 H1-Bars)
# -> Entry wird geskippt (unabhaengig von Session/Zone/Bias).
#   1.3 = moderat (filtert 2022Q3, 2024Q4)
#   1.2 = aggressiv (filtert alle Vola-Spikes)
#   1.5 = nur extreme Ausnahmen
VOLA_REGIME_FILTER_ENABLED = True
VOLA_REGIME_ATR_PERIOD = 14
VOLA_REGIME_MEDIAN_WINDOW = 200
VOLA_REGIME_MAX_RATIO = 1.3

# ----------------------------------------------------------------------
# D1-BIAS-FILTER  ***DEAKTIVIERT nach Ablation-Test (22.04.2026)***
# ----------------------------------------------------------------------
# Ablation-Ergebnis auf EURUSD shorts-only, 5J-Daten:
#   ALL_OFF       : 729 Trades,  +29.90R, PF 1.08  (Floor)
#   ONLY_D1BIAS   : 291 Trades,  -13.72R, PF 0.92  (FTMO FAIL!)
#   ONLY_VOLA     : 529 Trades,  +38.54R, PF 1.13
#   ONLY_SESSION  : 286 Trades,  +36.91R, PF 1.24  (beste Single-Filter)
#   ALL_ON        :  80 Trades,  -13.40R, PF 0.74  (FTMO FAIL!)
#
# Der D1-Bias-Filter entfernt systematisch die Gewinner (Delta: +43.62R
# in den gefilterten Trades). Bis Option-C-Diff gegen Baseline-Commit
# geklaert ist, Filter dauerhaft AUS. Vola + Session bleiben AN, diese
# liefern den Edge (+143.22% Portfolio-Return).
#
# Final-Portfolio mit D1_BIAS=False + CT/ADX-F3 (22.04.2026 v2):
#   EURUSD shorts-only (CT/ADX an): 195 Trades, +42.44R, PF 1.43
#   XAUUSD both       (CT/ADX an): 460 Trades, +73.14R, PF 1.27
#   USDJPY both       (EXCLUDE)  : 438 Trades, +36.42R, PF 1.14
#   Portfolio        : +172.92% / MaxDD -12.35% / FTMO OK
#
# Vergleich zu reiner Baseline ohne CT/ADX:
#   Return +29.70pp, MaxDD -4.79pp flacher, sum_R +16.05R besser.
#   2022 Killer-Jahr: +13.72% (Baseline hatte hier 2022Q3 den -54R-Crash).
D1_BIAS_FILTER_ENABLED = False      # DAUERHAFT AUS - siehe Ablation oben
D1_BIAS_EMA_PERIOD = 50

# ----------------------------------------------------------------------
# SESSION FILTER  (nur in Killzones traden)
# ----------------------------------------------------------------------
# Zeiten in UTC. Forex hat scharfe Liquiditaets-Peaks waehrend
# London Open (07-10 UTC) und NY Open (12-15 UTC). Die LDN/NY-Overlap
# (12-15 UTC) ist erfahrungsgemaess die sauberste Killzone fuer SMC.
# Asia (00-06) ist fuer M15-SMC meistens zu illiquid -> viele False-
# Breakouts und wide Spreads.
#
# Format: Liste von (start_hour_utc, end_hour_utc) Tupeln, inklusiv
# bis exklusiv. Beispiel (7, 10) = 07:00-09:59 UTC.
SESSION_FILTER_ENABLED = True
SESSION_KILLZONES_UTC = [
    (7, 10),    # London Open
    (12, 15),   # NY Open / LDN-NY Overlap
]
# Wochentage: 0=Montag, 6=Sonntag. Freitag-Nachmittag und Sonntag-Abend
# (typisch thin market) defaulten auf ausgeschlossen.
SESSION_SKIP_WEEKDAYS: list[int] = [5, 6]   # Sa, So komplett skippen
SESSION_SKIP_FRIDAY_AFTER_UTC = 20          # Fr ab 20:00 UTC kein Entry

# ---------------------------------------------------------------------------
# Per-Symbol Dead-Zone Blacklists (datenbasiert, Stand 22.04.2026)
# Quelle: session_net_view.py + session_stability.py (Jahr-für-Jahr Stabilitätscheck)
# ---------------------------------------------------------------------------

# Hours (UTC) die pro Symbol systematisch Geld verlieren
# Format: {"SYMBOL": [hour1, hour2, ...]}
SESSION_HOUR_BLACKLIST_PER_SYMBOL: dict[str, list[int]] = {
    "EURUSD": [13],   # 5/5 volle Jahre negativ, -3.87R gesamt, PF 0.75
}

# Weekdays die pro Symbol systematisch Geld verlieren (0=Mo, 1=Di, 2=Mi, 3=Do, 4=Fr)
SESSION_WEEKDAY_BLACKLIST_PER_SYMBOL: dict[str, list[int]] = {
    "USDJPY": [4],    # Fr: 4/5 volle Jahre neg, -10.26R gesamt, WR 30.59%, größter Single-Impact
}

# WATCHLIST (nicht gefiltert, aber beobachten):
# - USDJPY hour=9: Regime-Shift seit 2023 (+13.9R in 2021-22, -14.2R in 2023-26)
#   -> Im Oktober 2026 re-evaluieren. Wenn dann immer noch negativ, filtern.
# - XAUUSD hour=14: Verluste fast ausschließlich aus 2023 (-3.15R von -4.26R gesamt)
#   -> Instabil, nicht filtern. Bei neuem Backtest-Run prüfen.

# ----------------------------------------------------------------------
# COUNTER-TREND ADX FILTER  (H4-Context-Filter, Phase 5)
# ----------------------------------------------------------------------
# Identifiziert via killer_month_trend_analysis.py: Counter-Trend-Trades
# mit H4-ADX 15-25 sind systematisch toxisch (-31.3R im 5J-Backtest).
# CT mit ADX<15 (neutral, +1.78R) und CT mit ADX>=25 (Exhaustion,
# +22.52R) werden NICHT gefiltert.
#
# Validation F3 (simulate_filter.py + validate_filter_monthly_v2.py):
#   Return    143.22% -> 204.14%   (+60.92pp)
#   MaxDD     -17.14% -> -13.15%   ( -3.98pp)
#   Alle 6 echten Killer-Monate abgeschwaecht (inkl. 2023-04 +8.03R)
#   Normal-Monate netto +17.21R
#
# Toggle via --no-ct-adx in backtest_m15.py fuer Ablation.
CT_ADX_FILTER_ENABLED = True
CT_ADX_MIN_BLOCK      = 15.0          # unter 15: durchlassen
CT_ADX_MAX_BLOCK      = 25.0          # ab 25: durchlassen (Exhaustion)
CT_ADX_H4_EMA_FAST    = 20
CT_ADX_H4_EMA_SLOW    = 50
CT_ADX_H4_ADX_PERIOD  = 14
CT_ADX_H4_SLOPE_LB    = 3

# ----------------------------------------------------------------------
# CT/ADX PER-SYMBOL EXCLUDE
# ----------------------------------------------------------------------
# Auf diesen Symbolen wird der Filter NICHT angewandt, auch wenn
# CT_ADX_FILTER_ENABLED=True. Validation-Ergebnis (5J-Daten, 22.04.2026):
#   EURUSD: neutral-positiv  (PF 1.39->1.43, DD -12.37->-11.00, sumR -0.94R)
#   XAUUSD: stark positiv    (PF 1.18->1.27, DD ca.-6.37%, sumR +17.00R)
#   USDJPY: negativ          (PF 1.14->1.12, sumR -8.64R) -> ausschliessen
# Portfolio mit Exclude erwartet: Return > +160%, DD ca. -11%.
CT_ADX_FILTER_EXCLUDE_SYMBOLS = ["USDJPY"]

# ----------------------------------------------------------------------
# REGIME-OVERLAY  (Strategie 2 — Makro/Intermarket)
# ----------------------------------------------------------------------
# Setzt makro-getriebene Setups im Risk-Off-Regime aus (USDJPY beide Seiten).
# Signal in regime_overlay.py: Carry (US-JP-Renditedifferenz faellt) ODER
# VIX-Spike, durch Intermarket-Proxy (Yen+Gold bid) bestaetigt; bei fehlenden
# Makrodaten (data/macro/) Fallback auf den reinen Preis-Proxy.
# Validierung 14J (eval_overlay.py): MaxDD -21.7%->-15.6%, Return +90pp,
# OOS-robust, FTMO sauber.  Default AUS = v0.6-Paritaet; per --regime-overlay
# oder hier aktivieren.
REGIME_OVERLAY_ENABLED = False
REGIME_GATE_SYMBOLS    = ["USDJPY"]   # nur diese Symbole gaten
REGIME_GATE_SIDES      = None         # None = beide Seiten; oder ["long"]

# ----------------------------------------------------------------------
# INSTRUMENTS
# ----------------------------------------------------------------------
# Fuer SMC auf M15 eignen sich: grosse Indizes (US500/US30/GER40),
# Major-FX (EURUSD/USDJPY/GBPUSD), Gold. Crypto funktioniert auch,
# hat aber mehr noise.
INSTRUMENTS = {
    # ---- INDICES ----
    "US500":  {"ticker": "^GSPC",    "spread": 0.4,     "pip": 1.0,    "category": "index"},
    "US100":  {"ticker": "^NDX",     "spread": 1.5,     "pip": 1.0,    "category": "index"},
    "US30":   {"ticker": "^DJI",     "spread": 2.5,     "pip": 1.0,    "category": "index"},
    "GER40":  {"ticker": "^GDAXI",   "spread": 1.5,     "pip": 1.0,    "category": "index"},

    # ---- FOREX MAJORS ----
    "EURUSD": {"ticker": "EURUSD=X", "spread": 0.00010, "pip": 0.0001, "category": "forex"},
    "GBPUSD": {"ticker": "GBPUSD=X", "spread": 0.00015, "pip": 0.0001, "category": "forex"},
    "USDJPY": {"ticker": "USDJPY=X", "spread": 0.012,   "pip": 0.01,   "category": "forex"},

    # ---- COMMODITIES ----
    "XAUUSD": {"ticker": "GC=F",     "spread": 0.35,    "pip": 0.01,   "category": "commodity"},
}

ACTIVE_SYMBOLS = list(INSTRUMENTS.keys())
EXCLUDE_SYMBOLS: list[str] = []

# ----------------------------------------------------------------------
# DATEN
# ----------------------------------------------------------------------
# yfinance-Limits (fuer Prototyping/Smoketest):
#   M1:  7 Tage
#   M5:  60 Tage
#   M15: 60 Tage
#   H1:  730 Tage
#
# Fuer ernsthaften Backtest brauchst du MT5/Dukascopy CSV (3-5 Jahre M15).
# data_loader.py unterstuetzt beide Quellen.
DATA_SOURCE = "auto"                 # "auto" | "yfinance" | "csv"
DATA_DIR = "data"
RESULTS_DIR = "results"
YF_LOOKBACK_DAYS = {                 # wie weit yfinance rueckgreift
    "M1":  7,
    "M5":  60,
    "M15": 60,
    "H1":  730,
    "H4":  730,
    "D1":  5_000,
}

# CSV-Format (MT5/Dukascopy Export):
#   Header: datetime,open,high,low,close,volume
#   Delimiter: ","
#   Datetime: ISO-8601, "YYYY.MM.DD HH:MM" (MT5) oder "DD.MM.YYYY HH:MM:SS" (Dukascopy)
CSV_DATETIME_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y.%m.%d %H:%M:%S",
    "%Y.%m.%d %H:%M",
    "%Y-%m-%d %H:%M",
    "%d.%m.%Y %H:%M:%S.%f",   # Dukascopy: "02.01.2023 09:00:00.000"
    "%d.%m.%Y %H:%M:%S",       # Dukascopy ohne Millisek
    "%d.%m.%Y %H:%M",
]

# ----------------------------------------------------------------------
# E-MAIL (Gmail SMTP) - optional, noch nicht implementiert
# ----------------------------------------------------------------------
EMAIL_ENABLED = False
EMAIL_FROM = "bennarndtmelvin@gmail.com"
EMAIL_TO = "bennarndtmelvin@gmail.com"
EMAIL_SMTP_HOST = "smtp.gmail.com"
EMAIL_SMTP_PORT = 587
EMAIL_PASSWORD_ENV_VAR = "GMAIL_APP_PASSWORD"


# ----------------------------------------------------------------------
# Convenience
# ----------------------------------------------------------------------
def active_instruments():
    """Alle Instrumente nach Exclude-Filter."""
    return {sym: meta for sym, meta in INSTRUMENTS.items()
            if sym not in EXCLUDE_SYMBOLS}


CATEGORIES = ["forex", "index", "commodity", "crypto"]
