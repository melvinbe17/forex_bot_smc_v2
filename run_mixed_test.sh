#!/bin/bash
# run_mixed_test.sh
# -----------------
# Mixed-Mode-Portfolio-Test:
#   - EURUSD: Short-Only (Longs killen Alpha)
#   - XAUUSD: Long+Short (Longs bringen +6R)
#   - USDJPY: Long+Short (Longs bringen +17R)
#   - NAS100: weggelassen (FTMO-Fail in beiden Modi)
#
# Verwendung:  bash run_mixed_test.sh

set -e

RESULTS_DIR="results"

echo "=============================================="
echo "  MIXED-MODE PORTFOLIO TEST"
echo "=============================================="
echo

# Alte Trade-CSVs loeschen
echo "[1/3] Alte Trade-CSVs loeschen ..."
rm -f "${RESULTS_DIR}/trades_EURUSD.csv"
rm -f "${RESULTS_DIR}/trades_XAUUSD.csv"
rm -f "${RESULTS_DIR}/trades_USDJPY.csv"
rm -f "${RESULTS_DIR}/trades_NAS100.csv"
echo "      done."
echo

echo "[2/3] Backtests (per-Symbol Direction):"

echo
echo "--- EURUSD (shorts-only) ---"
python3 backtest_m15.py \
    --symbol EURUSD --limit 0 \
    --direction shorts-only \
    --trades-out "${RESULTS_DIR}/trades_EURUSD.csv" \
    --equity-out "${RESULTS_DIR}/equity_EURUSD.png"

echo
echo "--- XAUUSD (both) ---"
python3 backtest_m15.py \
    --symbol XAUUSD --limit 0 \
    --direction both \
    --trades-out "${RESULTS_DIR}/trades_XAUUSD.csv" \
    --equity-out "${RESULTS_DIR}/equity_XAUUSD.png"

echo
echo "--- USDJPY (both) ---"
python3 backtest_m15.py \
    --symbol USDJPY --limit 0 \
    --direction both \
    --trades-out "${RESULTS_DIR}/trades_USDJPY.csv" \
    --equity-out "${RESULTS_DIR}/equity_USDJPY.png"

echo
echo "[3/3] Portfolio-Aggregation (3-Symbol Mixed-Mode)"
python3 aggregate_multi.py EURUSD XAUUSD USDJPY

echo
echo "=============================================="
echo "  FERTIG. Vergleichswerte:"
echo "    3-Symbol Short-Only Baseline:"
echo "      Return +80.59%, MaxDD -13.62%"
echo "    4-Symbol Long+Short ueberall (letzter Run):"
echo "      Return +24.61%, MaxDD -17.48%"
echo "=============================================="