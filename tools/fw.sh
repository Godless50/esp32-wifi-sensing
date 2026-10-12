#!/usr/bin/env bash
# Build / flash firmware in the ESP-IDF container.  usage: tools/fw.sh build|flash
set -euo pipefail
cd "$(dirname "$0")/../firmware"
IMG=espressif/idf:release-v5.4
ARGS=(--rm -v "$PWD":/project -w /project -u "$(id -u):$(id -g)" -e HOME=/tmp)
case "${1:-build}" in
  build) docker run "${ARGS[@]}" "$IMG" idf.py build ;;
  flash) docker run "${ARGS[@]}" --group-add "$(getent group dialout | cut -d: -f3)" --device /dev/ttyACM0 \
           "$IMG" idf.py -p /dev/ttyACM0 flash ;;
  *) echo "usage: $0 build|flash" >&2; exit 1 ;;
esac
