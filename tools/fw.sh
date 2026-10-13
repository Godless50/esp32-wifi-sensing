#!/usr/bin/env bash
# Build / flash firmware in the ESP-IDF container.  usage: tools/fw.sh build | flash [port, default /dev/ttyACM0] | build-debug
set -euo pipefail
cd "$(dirname "$0")/../firmware"
IMG=espressif/idf:release-v5.4
ARGS=(--rm -v "$PWD":/project -w /project -u "$(id -u):$(id -g)" -e HOME=/tmp)
MAIN=(-D SDKCONFIG=sdkconfig_main.out -D SDKCONFIG_DEFAULTS=sdkconfig.defaults)
DEBUG=(-B build_debug -D SDKCONFIG=sdkconfig_debug.out -D "SDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.debug")
case "${1:-build}" in
  # рабочая прошивка: конфиг всегда собирается из sdkconfig.defaults (воспроизводимо из git), а не из ручного sdkconfig
  build) docker run "${ARGS[@]}" "$IMG" idf.py "${MAIN[@]}" build ;;
  # отладочная: + sdkconfig.debug (полная проверка кучи); замеры скорости с ней не сравнивать
  build-debug) docker run "${ARGS[@]}" "$IMG" idf.py "${DEBUG[@]}" build ;;
  flash) PORT=$(readlink -f "${2:-/dev/ttyACM0}")
         docker run "${ARGS[@]}" --group-add "$(getent group dialout | cut -d: -f3)" --device "$PORT" \
           "$IMG" idf.py "${MAIN[@]}" -p "$PORT" flash
         # сброс по RTS у части плат оставляет чип в загрузчике; сброс сторожевым таймером запускает прошивку
         docker run --rm --group-add "$(getent group dialout | cut -d: -f3)" --device "$PORT" "$IMG" \
           python -m esptool --chip esp32s3 -p "$PORT" --before no_reset --after watchdog_reset chip_id >/dev/null 2>&1 || true ;;
  # вариант для S3 с 8 МБ флеша без PSRAM: своя папка сборки и свой sdkconfig
  build-lite) docker run "${ARGS[@]}" "$IMG" idf.py -B build_lite -D SDKCONFIG=sdkconfig_lite.out \
                -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.lite" build ;;
  flash-lite) PORT=$(readlink -f "${2:?port}")
         docker run "${ARGS[@]}" --group-add "$(getent group dialout | cut -d: -f3)" --device "$PORT" \
           "$IMG" idf.py -B build_lite -D SDKCONFIG=sdkconfig_lite.out \
           -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.lite" -p "$PORT" flash
         # сброс по RTS у части плат оставляет чип в загрузчике; сброс сторожевым таймером запускает прошивку
         docker run --rm --group-add "$(getent group dialout | cut -d: -f3)" --device "$PORT" "$IMG" \
           python -m esptool --chip esp32s3 -p "$PORT" --before no_reset --after watchdog_reset chip_id >/dev/null 2>&1 || true ;;
  *) echo "usage: $0 build|flash [port]|build-debug|build-lite|flash-lite port" >&2; exit 1 ;;
esac
