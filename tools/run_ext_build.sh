#!/bin/bash
# Строит датасет предобучения из atanasuke (по проекту на процесс, без многопоточности внутри numpy).
R=/home/ops/esp32-wifi-sensing
cd "$R/ml" || exit 1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH="$R/server"
timeout 3000 "$R/.venv-rocm/bin/python" build_ext_atanasuke.py --src ../data/ext/atanasuke --out ../data/ml/${2:-ext_atanasuke}.npz --jobs "${1:-20}" > /tmp/ext.log 2>&1
echo "EXIT=$?" >> /tmp/ext.log
