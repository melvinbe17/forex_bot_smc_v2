===============================================================
  FOREX BOT SMC  (Smart Money Concepts / ICT - M15)
===============================================================

Parallelprojekt zu forex_bot_v3.
  * v3       = Daily-Trendfolge, ATR-Stops, Ensemble-Voting
  * smc      = M15-SMC-Strategie: BOS/CHoCH, OBs, FVGs, Liq-Sweeps,
               Premium/Discount, HTF-Bias-Filter

---------------------------------------------------------------
STAND (Session 1 / MVP)
---------------------------------------------------------------
FERTIG:
  config.py         - alle SMC-Parameter + FTMO-Settings
  data_loader.py    - yfinance + MT5/Dukascopy-CSV-Import
  smc_structure.py  - ATR, Swings, BOS, CHoCH, Premium/Discount
  smc_patterns.py   - Order Blocks, Fair Value Gaps, Liq Sweeps
                      + SMCSnapshot.analyze() als One-Call-API
  smc_demo.py       - CLI: Analyse + Plot fuer ein Symbol/TF
  _smoketest.py     - Offline-Tests (14 Checks, alle PASS)

NOCH NICHT:
  smc_strategy.py   - Multi-TF-Entry-Logic (HTF-Bias + LTF-Setup
                      + M1-BOS-Confirmation)
  backtest_m15.py   - M15-Event-Driven-Backtest mit Partials,
                      BE-Shift, 3-Loss-Dayout
  daily_signals.py  - Live-Runner fuer FTMO

---------------------------------------------------------------
1. INSTALLATION
---------------------------------------------------------------
    cd forex_bot_smc
    pip3 install -r requirements.txt --break-system-packages

---------------------------------------------------------------
2. SMOKETEST (offline, keine Internetverbindung noetig)
---------------------------------------------------------------
    python3 _smoketest.py

Erwartete Ausgabe: 14 Checks, alle PASS.  Das beweist dass die
Detektoren (Swings, BOS, CHoCH, OBs, FVGs, Liq-Sweeps, P/D-Zones)
korrekt arbeiten und kein Lookahead-Bias drin ist.

---------------------------------------------------------------
3. DEMO AUF YFINANCE-DATEN (schnell, aber limitiert)
---------------------------------------------------------------
yfinance liefert fuer M15 nur ca. 60 Tage zurueck - reicht als
Sanity-Check, nicht fuer ernsthaften Backtest.

    python3 smc_demo.py --symbol US500 --tf M15
    python3 smc_demo.py --symbol EURUSD --tf H1
    python3 smc_demo.py --symbol XAUUSD --tf M15

Output:
    Console-Summary (Swings, BOS, OBs, FVGs, Sweeps, P/D-Zone)
    results/smc_{symbol}_{tf}.png  - Chart mit markierten Zonen

---------------------------------------------------------------
4. ECHTE DATEN: MT5/DUKASCOPY CSV
---------------------------------------------------------------
Fuer den richtigen Backtest brauchst du 3-5 Jahre M15-History pro
Symbol (yfinance geht nicht). Zwei Quellen:

A) MT5-Export (gratis, wenn du schon Demo-Account hast):
   1) MT5 oeffnen
   2) Ansicht -> Symbole -> Symbol waehlen -> Balken oder Ticks
   3) Zeitrahmen M15 + Zeitraum waehlen
   4) Export als CSV

B) Dukascopy (gratis Browser-Tool):
   https://www.dukascopy.com/swiss/english/marketwatch/historical/
   -> Instrument + Zeitrahmen + Zeitraum + Format "CSV"

FORMAT, das der data_loader erwartet:
   Dateiname: data/{SYMBOL}_{TIMEFRAME}.csv
             z.B. data/EURUSD_M15.csv, data/US500_H1.csv

   Inhalt (Header erforderlich):
     datetime,open,high,low,close,volume
     2023-01-02 09:00:00,1.0712,1.0725,1.0710,1.0722,12345
     2023-01-02 09:15:00,1.0722,1.0730,1.0720,1.0728,10988
     ...

   Akzeptierte Datetime-Formate:
     2023-01-02 09:00:00        (ISO)
     2023-01-02T09:00:00        (ISO mit T)
     2023.01.02 09:00:00        (MT5 default)
     2023.01.02 09:00           (MT5 ohne Sekunden)

   Spalten-Aliase (werden auto-renamed):
     date/time/timestamp -> datetime
     o/h/l/c/v           -> open/high/low/close/volume
     tickvol/vol         -> volume

WICHTIG: Timeframe-Namen sind: M1, M5, M15, M30, H1, H4, D1.

Test nach dem Ablegen:
    python3 data_loader.py --symbol EURUSD --tf M15 --source csv

---------------------------------------------------------------
5. DIE STRATEGIE IN WORTEN  (aus dem Checklist-Bild)
---------------------------------------------------------------
Ablauf eines Trades:

  STUFE 1: HTF-BIAS  (H1/H4, Daily)
    -> Ist der Markt bullish oder bearish?
       = Letzter bestaetigter BOS auf H1 bestimmt die Richtung.
       Nur Longs in Bullish-Bias, nur Shorts in Bearish-Bias.

  STUFE 2: LIQUIDITY / SWEEP  (M15)
    -> Wurde ein offensichtliches Swing-High/Low "gesweept"?
       (Wick drueber, Close zurueck). Das ist der Trigger dass
       Retail-Stops eingesammelt wurden = Smart-Money steigt ein.

  STUFE 3: STRUKTUR-SHIFT / SETUP-ZONE  (M15)
    -> Nach dem Sweep muss eine CHoCH (kleiner TF) folgen - der
       erste Hinweis auf Richtungswechsel.
    -> Dann Markierung von OB oder FVG nahe der CHoCH-Origin.
    -> Setup-Zone MUSS auf der richtigen Seite von Equilibrium
       (50% Fib) liegen: Longs im Discount, Shorts im Premium.

  STUFE 4: ENTRY-CONFIRMATION  (M1, optional M5)
    -> Preis kommt in die OB/FVG-Zone.
    -> Entry: entweder M1-BOS in Trade-Richtung (agressiv) oder
       Limit an der Zone (konservativ).

  STUFE 5: TRADE-MANAGEMENT
    -> SL: hinter die Zone + 0.2x ATR Puffer.
    -> TP1: 1:2 R/R -> 50 % raus.
    -> TP2: 1:4 R/R -> weitere 25 % raus, Rest als Runner.
    -> Nach M1-BOS in Trade-Richtung: SL auf Break-Even.
    -> Max-Hold: 48 M15-Bars (= 12h).

  STUFE 6: DAYOUT
    -> 3 Stop-Losses hintereinander am selben Tag -> Bot stoppt
       fuer heute. (FTMO-Daily-Loss-Protection.)

---------------------------------------------------------------
6. ROADMAP
---------------------------------------------------------------
Naechste Session (direkt anknuepfbar):
  [ ] smc_strategy.py
        - multitf_bias(htf_df)            -> +1/-1/0
        - find_setups(htf, ltf)           -> List[SMCSetup]
        - confirm_entry(setup, entry_df)  -> Trade or None
  [ ] backtest_m15.py
        - event-driven, pro Bar: snap aktualisieren, Setups pruefen,
          Partials/BE/MaxHold handhaben
        - Shared-Account wie in v3 (Compounding + MAX_CONCURRENT)
  [ ] Integration: ein Durchlauf ueber deine MT5-CSVs, Reporting
        wie v3 (summary_shared_account.csv, equity_plots, ...)

Session 3:
  [ ] daily_signals.py: frueh morgens Bias pruefen, Setups melden
  [ ] Gmail-Integration wie in v3
  [ ] FTMO-Dry-Run dokumentieren

---------------------------------------------------------------
7. ARCHITEKTUR-DIAGRAMM (grob)
---------------------------------------------------------------
   data_loader      ->   HTF(H1)
                    ->   LTF(M15)       ->  analyze()  ->  SMCSnapshot
                    ->   ENTRY(M1)                         |
                                                           v
                                        smc_strategy.find_setups
                                                           |
                                                           v
                                        backtest_m15 / daily_signals

---------------------------------------------------------------
8. DISCLAIMER
---------------------------------------------------------------
SMC-Regeln sind diskretionaer beschrieben. Dieser Code uebersetzt
~70% der Rules in harte Logik. Erwartete Abweichungen:
  * Inducement: nicht explizit implementiert (wird aber durch den
    Liq-Sweep-Detektor teilweise mit abgedeckt).
  * "Displacement" und "Expansion" sind weich definiert -
    wir approximieren ueber FVG_MIN_SIZE_ATR.
  * OB-Variante: wir nehmen die *letzte* gegenlaeufige Kerze.
    Andere Definitionen (letzte VOR Displacement, schools differ)
    sind in config.OB_* konfigurierbar, aber nicht 100% deckend.

Backtestergebnisse sind keine Garantie fuer Live-Performance.
Erst auf FTMO-Free-Trial testen, dann echtes Kapital.
