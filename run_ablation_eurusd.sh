#!/bin/bash
# run_ablation_eurusd.sh
# Filter-Ablation fuer EURUSD shorts-only:
# Welcher der drei neuen Filter killt den Short-Edge?
# Baseline-Ziel: 158 Trades, +47.54R, PF 1.58

set -e

SYM="EURUSD"
OUT="results/_ablation_eurusd"
mkdir -p "${OUT}"

run_case () {
    local name="$1"
    shift
    echo
    echo "=============================================="
    echo "  ${name}"
    echo "=============================================="
    python3 backtest_m15.py \
        --symbol "${SYM}" --limit 0 \
        --direction shorts-only \
        "$@" \
        --trades-out "${OUT}/trades_${name}.csv" \
        --equity-out "${OUT}/equity_${name}.png" \
        2>&1 | tail -n 25
}

run_case "ALL_OFF"       --no-d1bias --no-vola --no-session
run_case "ONLY_D1BIAS"   --no-vola --no-session
run_case "ONLY_VOLA"     --no-d1bias --no-session
run_case "ONLY_SESSION"  --no-d1bias --no-vola
run_case "ALL_ON"

echo
echo "=============================================="
echo "  FERTIG. CSVs in ${OUT}/"
echo "  Vergleiche n_trades, sum_r, PF zwischen den Runs."
echo "=============================================="