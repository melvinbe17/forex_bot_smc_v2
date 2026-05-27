# STRATEGY 2 — Handoff & Kontext-Dokument

**Zweck:** Dieses Dokument überträgt das komplette Wissen aus der Entwicklung von **Strategie 1 (v0.6)** auf einen neuen Cowork-Chat, in dem **Strategie 2** gebaut/optimiert wird. Im neuen Chat: diesen Ordner verbinden und sagen *„lies STRATEGY2_HANDOFF.md"* — dann ist der gesamte Lern- und Entscheidungsstand sofort verfügbar.

**Ziel von Strategie 2:** Den **maximalen Drawdown senken** und den **durchschnittlichen Profit erhöhen** — aufbauend auf dem funktionierenden v0.6-Fundament, ggf. mit zusätzlicher **Fundamentaldaten-Analyse**. Strategie 1 (v0.6) läuft unangetastet live weiter; Strategie 2 ist eine getrennte Kopie.

---

## 1. Was Strategie 1 (v0.6) ist

SMC/ICT-Trading-Bot auf **M15**, der für **FTMO-Prop-Challenges** ausgelegt ist. Drei Symbole: **EURUSD, XAUUSD, USDJPY**. Multi-Timeframe-Ansatz:

- **HTF-Bias** aus **H1** (aus M15 resampled): letzter Struktur-Event BOS/CHoCH up → nur Longs, down → nur Shorts, sonst kein Trade.
- **LTF-Setup** auf **M15**: unmitigierte **FVG** (Order Blocks sind aus, weil im 5J-Test leicht negativ) im Discount/Premium der PD-Zone, getriggert durch eine Confirmation-Kerze.
- **Entry = Bar-Close** (Market-on-Close, keine Limit-Order).

## 2. Baseline-Zahlen (die Messlatte für Strategie 2)

**5-Jahre-Backtest (2021–2026, In-Sample, v0.6, 0,7 % Risk compound):**
- Portfolio: **+210,13 %** ($10k → $31.013), **PF 1,30**, **MaxDD −12,43 %**, **FTMO OK**, 988 Trades
- Per-Symbol: EURUSD PF 1,57 (shorts-only) · XAUUSD 1,27 (both) · USDJPY 1,24 (both, CT/ADX aus)

**14-Jahre-Backtest (2011–2026, Out-of-Sample):**
- Portfolio: **+311,26 %** ($10k → $41.126), **PF 1,13**, **MaxDD −21,67 %**, FTMO OK (statisch), 2.914 Trades
- Per-Symbol: EURUSD 1,18 · XAUUSD 1,14 · USDJPY 1,10
- **→ Wichtigste Erkenntnis:** PF fällt out-of-sample von 1,30 auf 1,13. Klares Overfitting-Signal. Die Filter sind auf 2021–2025 getunt.

**Monatsstatistik 2021–2026 (64 Monate):** 70 % positiv · Ø **+1,86 %/Monat** · Median +2,12 % · bester +12,96 % (Mai 2025) · schlechtester **−7,51 %** (Juni 2024) · Ø Gewinnmonat +3,71 % · Ø Verlustmonat −2,52 %.

**Jahre:** 2021 +43,3 % · 2022 +16,8 % · 2023 +7,8 % (schwach) · 2024 +19,9 % · 2025 +47,0 % (stark) · 2026 YTD −2,4 %.

**Killer-Jahre im 14J-Test (NICHT im 2021+-Fenster sichtbar):** 2019 −6,9 %, 2020 −5,5 % — zwei Verlustjahre in Folge. Das ist das eigentliche Risiko, das Strategie 2 adressieren sollte.

## 3. Trade-Mechanik / Management (backtest_m15.py simulate)

- Entry = Setup.entry_price (Bar-Close), 1 Position pro Symbol (kein Pyramiding)
- SL = Zone ± Buffer (`SL_BUFFER_PCT` 0.0005)
- **TP1 = 2R → 50 % schließen, dann SL auf Break-Even**
- **TP2 = 4R → 25 % schließen**
- **Runner = 25 %** bis SL/BE oder Max-Hold
- **Max-Hold = 48 M15-Bars (12h)**
- **Max 3 Verluste/Tag** → Stopp für den Tag
- SL-first (pessimistisch) bei Bar, die SL und TP enthält
- Risk: Backtest 0,75 % (`RISK_PER_TRADE`), **live 0,5 %** (Go/No-Go-Auflage)

## 4. Filter (alle in config.py, zur Laufzeit gelesen)

- **Session-Killzones (UTC):** (7–10) London Open, (12–15) NY/Overlap. Sa/So komplett aus, Fr ab 20:00 UTC aus.
- **Per-Symbol-Dead-Zones (datenbasiert):** EURUSD Stunde 13 aus (5/5 Jahre negativ), USDJPY Freitag aus (4/5 Jahre negativ).
- **Vola-Regime-Filter:** H1-ATR(14) > 1,3 × rolling-Median(200) → Entry skippen (filtert Vola-Spikes wie 2022Q3, 2024Q4).
- **CT/ADX-Filter (H4):** Counter-Trend-Trades bei ADX 15–25 blocken (toxisch). ADX<15 und ≥25 durchlassen. **Exclude USDJPY** (dort negativ).
- **D1-Bias-Filter: AUS** (Ablation zeigte: entfernt systematisch die Gewinner).
- **Direction:** EURUSD shorts-only (Config-Default), XAUUSD & USDJPY both.
- **Watchlist (beobachten, Okt 2026 re-checken):** USDJPY h9 Regime-Shift seit 2023, XAUUSD h14 Instabilität.

## 5. Datenpipeline

- **Dukascopy M15 Parquet**, ~14 Jahre je Symbol, in `data/dukascopy/*.parquet` (EURUSD ab 2012, XAUUSD/USDJPY ab 2011).
- Geladen via `data_loader.py` (`load_parquet`, Title-Case OHLCV, naiv UTC), Download via `dukascopy_loader.py`.
- Backtest: `python backtest_m15.py --symbol EURUSD --source parquet --limit 0 ...` ; Portfolio: `python aggregate_multi.py EURUSD XAUUSD USDJPY`.

## 6. Code-Architektur

**Strategie/Backtest-Kern:**
- `smc_structure.py` — Swings, BOS/CHoCH-Events
- `smc_patterns.py` — analyze(): OB/FVG/PD-Zonen, unmitigated_obs/fvgs
- `smc_strategy.py` — find_setup / find_all_setups, htf_bias_at, Session-/Vola-/D1-/CT-ADX-Gating, Setup-Dataclass
- `ct_adx_filter.py` — H4-ADX/EMA-Indikatoren + should_block_setup_at_time
- `backtest_m15.py` — event-driven Simulation (Trade-Management), Metriken
- `aggregate_multi.py` — Portfolio-Aggregation (shared account, compound, FTMO-Check)
- `config.py` — ALLE Parameter zentral
- `data_loader.py` / `dukascopy_loader.py` — Daten

**Analyse-Tooling (sehr relevant für Strategie 2!):**
- `dd_attribution.py` — Drawdown-Attribution (welche Trades/Phasen verursachen DD)
- `simulate_dynamic_risk.py` — dynamische Risk-Modelle (es gibt `results/dynamic_risk_monthly_stufe_A..L.csv` — diverse Risk-Stufen schon getestet!)
- `killer_month_trend_analysis.py` — Analyse der toxischen Monate (hat CT/ADX-Filter hervorgebracht)
- `simulate_filter.py` / `validate_filter_monthly(_v2).py` — Filter-Validierung Monat für Monat
- `session_net_view.py` / `session_stability.py` — Per-Hour/Weekday Net-R + Jahr-für-Jahr-Stabilität (hat Dead-Zones hervorgebracht)
- `oos_2026.py`, `analyze_trades.py` — OOS-Check, Trade-Analyse

**Live-Infra (Phase 6, läuft für v0.6 — für Strategie 2 erst relevant wenn sie validiert ist):**
- `live_feed.py` (MT5→OHLCV, Server-Zeit→UTC +3h Offset), `live_runner.py` (M15-Loop, Per-Symbol-Config-Parität, Heartbeat), `executor.py` (Lot-Sizing, Order-Management, MAGIC 6020260), `ftmo_guard.py` (Daily/Total Kill-Switch, Trip bei 80 % = −4 %/−8 %), `notify.py` (Gmail-Alerts), `heartbeat.py`, `daily_summary.py`, `run_bot.bat` (Auto-Restart).

## 7. Live-Setup (Strategie 1, zur Info)

ForexVPS „Core" (Windows Server 2022, London) · MT5 „FTMO Global Markets" · FTMO-Demo (Login 1513509811, Server FTMO-Demo, $10k) · Python 3.12 x64 + MetaTrader5 · `GMAIL_APP_PASSWORD` als Env-Var · `schtasks` Tages-Mail 22:00 · Bot läuft via `run_bot.bat` (Auto-Restart). **Strategie 2 braucht das alles erst, wenn sie validiert ist — zuerst nur Backtest/Research.**

## 8. Hart erkaufte Lehren (Fallstricke)

- **Overfitting ist real:** In-Sample (2021–25) PF 1,30 → Out-of-Sample (2011–25) 1,13. Jede Optimierung MUSS gegen Out-of-Sample / walk-forward geprüft werden, sonst tunt man nur Rauschen.
- **MT5 Server-Zeit = EEST (UTC+3)** → muss auf UTC umgerechnet werden, sonst feuert der Session-Filter verschoben.
- **MT5 erlaubt nur 1 Python-Verbindung** gleichzeitig → Monitoring (daily_summary) liest die `heartbeat.json` statt eigener MT5-Verbindung.
- **Windows-Konsole QuickEdit-Modus** friert den Bot ein, wenn man reinklickt → QuickEdit aus.
- MT5-Filling-Mode über Roh-Bitmaske (FOK=1, IOC=2), echten Fill-Preis aus `position.price_open` (nicht `result.price`).
- Risk-Modell Backtest: 1R = fixer $-Betrag pro Per-Symbol-Run; Portfolio-Aggregator nutzt Compounding (% der aktuellen Equity).

## 9. Optimierungs-Ansätze für Strategie 2 (Brainstorm-Startpunkte)

**Ziel: MaxDD runter, Ø Profit hoch.** Konkrete Hebel:

1. **Dynamisches Risk** — es gibt schon `simulate_dynamic_risk.py` + Ergebnisdateien (Stufen A–L). Idee: nach Verlustserie Risk drosseln, in guten Phasen normal. Senkt DD.
2. **DD-Attribution vertiefen** (`dd_attribution.py`, offener Track #52) — welche Setups/Stunden/Regime verursachen die −7,5 %-Monate? Gezielt filtern.
3. **Setup-Finder-Optimierung** (offener Track #47) — Ziel war PF 1,39 → 1,58. Walk-forward statt fixe Dead-Zones.
4. **Fundamentaldaten (neu, Strategie-2-Kern-Idee):** Wirtschaftskalender (High-Impact-News → Entries pausieren/SL weiten), Zinsdifferenzen (Carry-Bias für USDJPY), COT-Daten (Positionierung), Risk-On/Off-Regime. Könnte die Killer-Jahre 2019/2020 abfedern.
5. **Regime-Filter** — die Verlustphasen clustern. Ein übergeordneter Regime-Schalter (Trend vs. Range, Vola-Quantil) könnte ganze toxische Phasen aussparen.
6. **Walk-Forward-Reoptimierung** der Dead-Zones alle 12 Monate statt fix.

## 10. Methodik-Regeln (nicht verhandelbar)

- Immer **In-Sample vs. Out-of-Sample** trennen. Auf 14J Dukascopy validieren, nicht nur 2021+.
- **Walk-forward** denken: Optimierung darf nicht die Zukunft kennen.
- FTMO-Regeln immer mitprüfen: Daily −5 %, Total −10 %.
- Jede Änderung gegen die v0.6-Baseline (Tabelle in §2) messen — verbessert sie DD UND/ODER Ø-Profit, ohne PF/FTMO zu verschlechtern?
- Verbesserung am In-Sample, die im Out-of-Sample zerfällt, ist **keine** Verbesserung.

## 11. Setup-Schritte im neuen Chat

1. Dieser Ordner ist eine Kopie von v0.6 → erst die git-Anbindung trennen, damit Strategie 2 nicht in v0.6's Repo pusht: im v2-Ordner `rm -rf .git`, dann optional `git init` + neues GitHub-Repo (z.B. `forex_bot_smc_v2`).
2. Backtest-Engine läuft sofort (Daten + Code sind mitkopiert). Testlauf: `python backtest_m15.py --symbol EURUSD --source parquet --limit 0`.
3. Optimierung anfangen — am besten mit DD-Attribution + dynamischem Risk (die größten Hebel für „DD runter").

---

*Erstellt am Ende der v0.6-Live-Deployment-Session. Strategie 1 läuft zu diesem Zeitpunkt voll automatisch und überwacht auf dem FTMO-Demo.*
