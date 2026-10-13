#!/bin/bash
# Живое распознавание + онлайн-дообучение (ml/learner.py) на видеокарте сервера. Лог: data/ml/learner.log
R=/home/ops/esp32-wifi-sensing
cd "$R/ml" || exit 1
export HSA_OVERRIDE_GFX_VERSION=10.3.0 MIOPEN_LOG_LEVEL=3 PYTHONPATH="$R/server" OMP_NUM_THREADS=4 PYTHONWARNINGS=ignore
exec "$R/.venv-rocm/bin/python" learner.py "$@" >> "$R/data/ml/learner.log" 2>&1
