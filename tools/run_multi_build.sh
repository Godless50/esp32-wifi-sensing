#!/bin/bash
# Синтез данных «несколько людей + поза» (ml/build_multi.py) в фоне; лог /tmp/multi.log
R=/home/ops/esp32-wifi-sensing
cd "$R/ml" || exit 1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH="$R/server"
timeout 3000 "$R/.venv-rocm/bin/python" build_multi.py --data ../data --out ../data/ml/${1:-multi_v1}.npz \
  --mix "${2:-4000}" --physics "${3:-16000}" --jobs 16 > /tmp/multi.log 2>&1
echo "EXIT=$?" >> /tmp/multi.log
