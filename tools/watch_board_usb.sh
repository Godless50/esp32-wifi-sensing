#!/bin/bash
# Ждёт, пока плата с MAC $1 появится на USB (до $2 секунд), и пишет её консоль 60 с без сброса в $3.
# usage: setsid tools/watch_board_usb.sh 02:00:00:00:c9:04 900 /home/ops/shots/board2_serial.log &
R=/home/ops/esp32-wifi-sensing
MAC=$1; LIMIT=${2:-900}; OUT=${3:-/tmp/board_serial.log}
end=$((SECONDS + LIMIT))
while [ $SECONDS -lt $end ]; do
  DEV=$(ls /dev/serial/by-id/ 2>/dev/null | grep -i "$MAC" | head -1)
  if [ -n "$DEV" ]; then
    PORT=$(readlink -f "/dev/serial/by-id/$DEV")
    echo "found $DEV -> $PORT at $(date +%T)" > "$OUT"
    docker run --rm --device "$PORT" --group-add "$(getent group dialout | cut -d: -f3)" -v "$R/tools:/t" wsn-server \
      python /t/serial_cmd.py --port "$PORT" --wait 60 "status" >> "$OUT" 2>&1
    echo "done $(date +%T)" >> "$OUT"
    exit 0
  fi
  sleep 1
done
echo "not found in ${LIMIT}s" > "$OUT"
