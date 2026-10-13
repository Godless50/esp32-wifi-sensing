#!/bin/bash
# Список дополнительных передатчиков платы — по воздуху (до 14 MAC). Плата сохраняет его в NVS и перезапускается.
# usage: tools/push_links.sh <MAC платы> <mac1,mac2,...> [promisc 0|1] [beacon_hz]
#   promisc 0: не слушать эфир (библиотека Wi-Fi падает в режиме прослушивания); тогда видны только своя точка
#   доступа и кадры ESP-NOW других плат (каждая рассылает их с частотой beacon_hz, по умолчанию 50 Гц).
set -euo pipefail
R=/home/ops/esp32-wifi-sensing
DEV=${1:?board MAC}
LINKS=${2:?comma-separated MACs}
PROMISC=${3:-}
BEACON=${4:-50}
F="links_${DEV//:/}.wsnc"
python3 - "$LINKS" "$R/data/ml/$F" "$PROMISC" "$BEACON" <<'EOF'
import sys
macs = [m for m in sys.argv[1].split(",") if m]
assert len(macs) <= 14, "at most 14 extra transmitters"
out = bytearray(b"WSNC") + bytes([len(macs)])
for m in macs:
    b = bytes.fromhex(m.replace(":", ""))
    assert len(b) == 6, m
    out += b
if sys.argv[3] != "":
    out += bytes([1 if sys.argv[3] == "1" else 0, max(0, min(100, int(sys.argv[4])))])
open(sys.argv[2], "wb").write(out)
EOF
curl -s -m10 -X POST http://127.0.0.1:8080/api/model/push -H 'content-type: application/json' \
  -d "{\"device_id\":\"$DEV\",\"file\":\"$F\"}"
echo
