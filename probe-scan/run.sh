#!/usr/bin/env bash
# Build, flash and capture serial output of the probe firmware.
set -euo pipefail
cd "$(dirname "$0")"
IMG=espressif/idf:release-v5.4
DIALOUT_GID=$(getent group dialout | cut -d: -f3)
docker run --rm -v "$PWD":/project -w /project -u "$(id -u):$(id -g)" \
  --group-add "$DIALOUT_GID" --device /dev/ttyACM0 -e HOME=/tmp "$IMG" \
  idf.py -p /dev/ttyACM0 build flash > build.log 2>&1
sleep 2
stty -F /dev/ttyACM0 115200 raw -echo -hupcl
timeout "${CAPTURE_S:-110}" cat /dev/ttyACM0 > scan.log || true
echo CAPTURE_DONE >> build.log
