#!/bin/bash
# run_fair_compare.sh
# Fairer Vergleich: selber Code, selbe Filter, nur Direction variiert.

set -e

RESULTS_DIR="results"
TMP_A="${RESULTS_DIR}/_A_short_only"
TMP_B="${RESULTS_DIR}/_B_long_short"

mkdir -p "${TMP_A}" "${TMP_B}"

echo "=============================================="
echo "  RUN A: SHORTS-ONLY (aktueller Code)"
echo "=============================================="

for sym in EURUSD XAUUSD USDJPY; do
    echo
    echo "--- ${sym} shorts-only ---"
    python3 backtest_m15.py \
        --symbol "${sym}" --limit 0 \
        --direction shorts-only \
        --trades-out "${TMP_A}/trades_${sym}.csv" \
        --equity-out "${TMP_A}/equity_${sym}.png"
    cp "${TMP_A}/trades_${sym}.csv" "${RESULTS_DIR}/trades_${sym}.csv"
done

echo
echo "--- Portfolio A (Shorts-Only) ---"
python3 aggregate_multi.py EURUSD XAUUSD USDJPY
cp "${RESULTS_DIR}/trades_portfolio.csv"  "${TMP_A}/trades_portfolio.csv"
cp "${RESULTS_DIR}/equity_portfolio.csv"  "${TMP_A}/equity_portfolio.csv"

echo
echo "=============================================="
echo "  RUN B: LONG+SHORT (aktueller Code)"
echo "=============================================="

for sym in EURUSD XAUUSD USDJPY; do
    echo
    echo "--- ${sym} both ---"
    python3 backtest_m15.py \
        --symbol "${sym}" --limit 0 \
        --direction both \
        --trades-out "${TMP_B}/trades_${sym}.csv" \
        --equity-out "${TMP_B}/equity_${sym}.png"
    cp "${TMP_B}/trades_${sym}.csv" "${RESULTS_DIR}/trades_${sym}.csv"
done

echo
echo "--- Portfolio B (Long+Short) ---"
python3 aggregate_multi.py EURUSD XAUUSD USDJPY
cp "${RESULTS_DIR}/trades_portfolio.csv"  "${TMP_B}/trades_portfolio.csv"
cp "${RESULTS_DIR}/equity_portfolio.csv"  "${TMP_B}/equity_portfolio.csv"

echo
echo "=============================================="
echo "  VERGLEICH (beide mit aktuellem Code / Filtern):"
echo "  Details siehe ${TMP_A}/ und ${TMP_B}/"
echo "=============================================="