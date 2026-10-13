#!/bin/bash
# Модель сервера после онлайн-дообучения -> int8 для платы -> отправка по воздуху (POST /api/model/push).
# Отправляет, только если int8 почти совпадает с float (зона и действие >= 0.95 на размеченных окнах).
# usage: tools/push_board_model.sh [папка модели, по умолчанию data/ml/live_model_s2] [MAC платы]
# Журнал: data/ml/board_push.log
R=/home/ops/esp32-wifi-sensing
MD=${1:-$R/data/ml/live_model_s2}
DEV=${2:-7c:4f:ad:bc:9b:54}
LOG=$R/data/ml/board_push.log
cd "$R" || exit 1
say() { echo "$(date '+%F %T') $*" >> "$LOG"; }
rm -rf data/ml/board_export && cp -r "$MD" data/ml/board_export || { say "copy of $MD failed"; exit 1; }
V=$(python3 -c "import json;print(json.load(open('data/ml/board_export/meta.json'))['version'])")
tools/gpu_run.sh /tmp/expw_push.log export_weights.py --model ../data/ml/board_export --net 0
grep -q "EXIT=0" /tmp/expw_push.log || { say "v$V: export_weights failed"; exit 1; }
docker run --rm -u "$(id -u):$(id -g)" -v "$PWD/data":/data -v "$PWD/ml":/ml -w /ml tensorflow/tensorflow:2.17.0 \
  python /ml/export_tflite.py --model /data/ml/board_export > /tmp/export_push.log 2>&1 || { say "v$V: export_tflite failed"; exit 1; }
AG=$(grep -o "agreement on scripted windows: {.*}" /tmp/export_push.log)
OK=$(python3 -c "
import ast,sys
d=ast.literal_eval(sys.argv[1].split(': ',1)[1])
print(int(d['zone']>=0.95 and d['act']>=0.95))" "$AG" 2>/dev/null)
if [ "$OK" != "1" ]; then say "v$V: int8 differs from float ($AG), not pushed"; exit 1; fi
cp data/ml/board_export/board_model.bin "data/ml/board_model_v$V.bin"
RES=$(curl -s -m10 -X POST http://127.0.0.1:8080/api/model/push -H 'content-type: application/json' \
  -d "{\"device_id\":\"$DEV\",\"file\":\"board_model_v$V.bin\"}")
say "v$V -> $DEV ($AG): $RES"
