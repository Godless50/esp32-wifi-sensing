#!/bin/bash
# Полная пересборка живой модели: последовательности наших записей, внешний датасет, предобучение, итоговый ансамбль.
set -e
R=/home/ops/esp32-wifi-sensing
cd "$R"
docker run --rm -u "$(id -u):$(id -g)" -v "$PWD/server":/app -v "$PWD/data":/data -v "$PWD/ml":/ml -w /ml -e PYTHONPATH=/app \
  wsn-ml python /ml/build_seq.py --sessions 20261013-053501-ee64,20261013-065019-45cd,20261013-050259-94cd,20261013-042804-6eba
tools/run_ext_build.sh 20
grep -q "EXIT=0" /tmp/ext.log
tools/gpu_run.sh /tmp/pretrain2.log pretrain_ext.py --skip-loso --out ../data/ml/pretrain_v2
grep -q "EXIT=0" /tmp/pretrain2.log
tools/gpu_run.sh /tmp/final2.log train_final.py --encoder ../data/ml/pretrain_v2/encoder.pt --out ../data/ml/live_model_v2
grep -q "EXIT=0" /tmp/final2.log
echo REBUILD_DONE
