#!/usr/bin/env bash
# Прошивка платы без кнопок: команда консоли "dl" -> загрузчик, запись, запуск сбросом сторожевым таймером.
# usage: tools/flash_dl.sh <port|by-id path> main|lite
set -euo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"
PORT=$(readlink -f "${1:?port}")
case "${2:?main|lite}" in
  main) B=build; SIZE=16MB ;;
  lite) B=build_lite; SIZE=8MB ;;
  *) echo "variant: main|lite" >&2; exit 1 ;;
esac
IMG=espressif/idf:release-v5.4
GRP=$(getent group dialout | cut -d: -f3)
python3 "$R/tools/serial_cmd.py" --port "$PORT" --wait 1 "" dl >/dev/null 2>&1 || true
sleep 2
docker run --rm -v "$R/firmware":/project -w /project/$B -u "$(id -u):$(id -g)" -e HOME=/tmp --group-add "$GRP" \
  --device "$PORT" "$IMG" python -m esptool --chip esp32s3 -p "$PORT" -b 460800 --before no_reset --after no_reset \
  write_flash @flash_args
docker run --rm --group-add "$GRP" --device "$PORT" "$IMG" \
  python -m esptool --chip esp32s3 -p "$PORT" --before no_reset --after watchdog_reset read_mac >/dev/null
echo "flashed $PORT ($2)"
