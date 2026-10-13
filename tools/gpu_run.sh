#!/bin/bash
# Запуск ml-скрипта на видеокарте сервера в фоне: tools/gpu_run.sh <log> <script.py> [args...]
R=/home/ops/esp32-wifi-sensing
log=$1; shift
cd "$R/ml" || exit 1
export HSA_OVERRIDE_GFX_VERSION=10.3.0 MIOPEN_LOG_LEVEL=3 PYTHONPATH="$R/server" OMP_NUM_THREADS=8
timeout ${GPU_TIMEOUT:-7200} "$R/.venv-rocm/bin/python" "$@" > "$log" 2>&1
echo "EXIT=$?" >> "$log"
