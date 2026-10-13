#!/bin/bash
# Модель для платы (arch s2): предобучение, итоговый ансамбль, честная проверка, экспорт int8.
set -e
R=/home/ops/esp32-wifi-sensing
cd "$R"
tools/gpu_run.sh /tmp/pretrain3.log pretrain_ext.py --out ../data/ml/pretrain_v3
grep -q "EXIT=0" /tmp/pretrain3.log
tools/gpu_run.sh /tmp/final3.log train_final.py --encoder ../data/ml/pretrain_v3/encoder.pt --out ../data/ml/live_model_v3
grep -q "EXIT=0" /tmp/final3.log
tools/gpu_run.sh /tmp/expw3.log export_weights.py --model ../data/ml/live_model_v3 --net 0
docker run --rm -u "$(id -u):$(id -g)" -v "$PWD/data":/data -v "$PWD/ml":/ml -w /ml tensorflow/tensorflow:2.17.0 \
  python /ml/export_tflite.py --model /data/ml/live_model_v3 > /tmp/exptfl3.log 2>&1
echo REBUILD_DONE
