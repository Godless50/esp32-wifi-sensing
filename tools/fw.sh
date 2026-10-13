#!/usr/bin/env bash
# Build / flash firmware in the ESP-IDF container.  usage: tools/fw.sh build | flash [port, default /dev/ttyACM0]
set -euo pipefail
cd "$(dirname "$0")/../firmware"
IMG=espressif/idf:release-v5.4
ARGS=(--rm -v "$PWD":/project -w /project -u "$(id -u):$(id -g)" -e HOME=/tmp)
case "${1:-build}" in
  build) docker run "${ARGS[@]}" "$IMG" idf.py build ;;
  flash) PORT=$(readlink -f "${2:-/dev/ttyACM0}")
         docker run "${ARGS[@]}" --group-add "$(getent group dialout | cut -d: -f3)" --device "$PORT" \
           "$IMG" idf.py -p "$PORT" flash ;;
  *) echo "usage: $0 build|flash" >&2; exit 1 ;;
esac
