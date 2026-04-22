#!/bin/bash
# run_best_portfolio.sh - 3-Symbol-Portfolio mit D1_BIAS AUS
set -e

OUT="results/_best_portfolio"
mkdir -p "${OUT}"

echo "=============================================="
echo "  BEST-PORTFOLIO RUN  (D1_BIAS=AUS, Vola+Session=AN)"
echo "=============================================="

echo ""
echo "[1/4] Alte Trade-CSVs loeschen ..."
rm -f results/trades_EURUSD.csv results/trades_XAUUSD.csv results/trades_USDJPY.csv
echo "      done."

echo ""
echo "=============================================="
echo "  [2/4] EURUSD  shorts-only  (D1_BIAS=AUS)"
echo "=============================================="
python3 backtest_m15.py --symbol EURUSD --limit 0 \
    --direction shorts-only --no-d1bias \
    --trades-out results/trades_EURUSD.csv \
    --equity-out "${OUT}/equity_EURUSD.png"

echo ""
echo "=============================================="
echo "  [3/4] XAUUSD  both  (D1_BIAS=AUS)"
echo "=============================================="
python3 backtest_m15.py --symbol XAUUSD --limit 0 \
    --direction both --no-d1bias \
    --trades-out results/trades_XAUUSD.csv \
    --equity-out "${OUT}/equity_XAUUSD.png"

echo ""
echo "=============================================="
echo "  [4/4] USDJPY  both  (D1_BIAS=AUS)"
echo "=============================================="
python3 backtest_m15.py --symbol USDJPY --limit 0 \
    --direction both --no-d1bias \
    --trades-out results/trades_USDJPY.csv \
    --equity-out "${OUT}/equity_USDJPY.png"

echo ""
echo "=============================================="
echo "  PORTFOLIO-AGGREGATION (3-Symbol Shared-Account)"
echo "=============================================="
python3 aggregate_multi.py EURUSD XAUUSD USDJPY

cp results/trades_portfolio.csv "${OUT}/" 2>/dev/null || true
cp results/equity_portfolio.csv "${OUT}/" 2>/dev/null || true
cp results/equity_portfolio.png "${OUT}/" 2>/dev/null || true

echo ""
echo "=============================================="
echo "  FERTIG. Outputs in ${OUT}/"
echo "  Vergleich:"
echo "    Alte Baseline    :  +80.59% / MaxDD -13.62%"
echo "    EURUSD-Baseline  :  158 Trades, +47.54R, PF 1.58"
echo "=============================================="
