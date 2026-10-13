#!/bin/bash
# Прошивка по воздуху: собранный образ -> data/ml/fw_<id>.bin -> POST /api/model/push (тот же канал, что модель).
# usage: tools/push_fw.sh main|lite <MAC платы>
#   main — firmware/build (16 МБ флеша, PSRAM: платы 1 и 2), lite — firmware/build_lite (8 МБ без PSRAM: плата 3).
# Плата пишет образ во второй раздел приложения, проверяет его, загружается и подтверждает только после выхода
# в Wi-Fi; иначе через 2 минуты загрузчик возвращает прежнюю прошивку. Ход: GET /api/model/push.
set -euo pipefail
R=/home/ops/esp32-wifi-sensing
case "${1:?main|lite}" in
  main) BIN=$R/firmware/build/wsn_fw.bin ;;
  lite) BIN=$R/firmware/build_lite/wsn_fw.bin ;;
  *) echo "variant: main|lite" >&2; exit 1 ;;
esac
DEV=${2:?board MAC}
ID=$(python3 -c "
import sys
b=open(sys.argv[1],'rb').read()
assert b[0]==0xE9 and b[0x20:0x24]==bytes.fromhex('3254cdab'), 'not an ESP app image'
s=b[0x20+144:0x20+147]; print('%08x' % (0x80000000|(s[0]<<16)|(s[1]<<8)|s[2]))" "$BIN")
cp "$BIN" "$R/data/ml/fw_$ID.bin"
curl -s -m10 -X POST http://127.0.0.1:8080/api/model/push -H 'content-type: application/json' \
  -d "{\"device_id\":\"$DEV\",\"file\":\"fw_$ID.bin\"}"
echo
echo "firmware $ID -> $DEV"
