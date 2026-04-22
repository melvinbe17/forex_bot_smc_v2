#!/bin/bash
# run_long_test.sh
# ----------------
# Backtest-Runner fuer Long+Short vs. Short-Only Vergleich.
# Fuehrt alle 4 Symbole durch und aggregiert das Portfolio.
#
# Verwendung:
#     bash run_long_test.sh
#
# Voraussetzung:
#     - config.py: TRADE_LONGS = True, TRADE_SHORTS = True
#     - data/EURUSD_M15.csv, XAUUSD_M15.csv, USDJPY_M15.csv, NAS100_M15.csv

set -e

SYMBOLS=("EURUSD" "XAUUSD" "USDJPY" "NAS100")
RESULTS_DIR="results"

echo "=============================================="
echo "  LONG+SHORT BACKTEST-RUN"
echo "=============================================="
echo

# Alte Trade-CSVs loeschen (damit keine stale Short-Only Ergebnisse drin bleiben)
echo "[1/3] Alte Trade-CSVs loeschen ..."
for sym in "${SYMBOLS[@]}"; do
    rm -f "${RESULTS_DIR}/trades_${sym}.csv"
done
echo "      done."
echo

echo "[2/3] Backtests pro Symbol (kompletter Datensatz, --limit 0)"
for sym in "${SYMBOLS[@]}"; do
    echo
    echo "--- ${sym} ---"
    python3 backtest_m15.py \
        --symbol "${sym}" \
        --limit 0 \
        --trades-out "${RESULTS_DIR}/trades_${sym}.csv" \
        --equity-out "${RESULTS_DIR}/equity_${sym}.png"
done

echo
echo "[3/3] Portfolio-Aggregation (4-Symbol Shared-Account)"
python3 aggregate_multi.py "${SYMBOLS[@]}"

echo
echo "=============================================="
echo "  FERTIG. Vergleichswerte Baseline:"
echo "    3-Symbol Short-Only (EUR+XAU+USDJPY):"
echo "      Return +80.59%, MaxDD -13.62%"
echo "    4-Symbol Short-Only (+NAS100):"
echo "      Return +62.07%  (NAS100 hat gekillt)"
echo "=============================================="
