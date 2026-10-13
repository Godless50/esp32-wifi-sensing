#!/bin/bash
# Список дополнительных передатчиков платы — по воздуху (до 14 MAC). Плата сохраняет его в NVS и перезапускается.
# usage: tools/push_links.sh <MAC платы> <mac1,mac2,...>
set -euo pipefail
R=/home/ops/esp32-wifi-sensing
DEV=${1:?board MAC}
LINKS=${2:?comma-separated MACs}
F="links_${DEV//:/}.wsnc"
python3 - "$LINKS" "$R/data/ml/$F" <<'EOF'
import sys
macs = [m for m in sys.argv[1].split(",") if m]
assert len(macs) <= 14, "at most 14 extra transmitters"
out = bytearray(b"WSNC") + bytes([len(macs)])
for m in macs:
    b = bytes.fromhex(m.replace(":", ""))
    assert len(b) == 6, m
    out += b
open(sys.argv[2], "wb").write(out)
EOF
curl -s -m10 -X POST http://127.0.0.1:8080/api/model/push -H 'content-type: application/json' \
  -d "{\"device_id\":\"$DEV\",\"file\":\"$F\"}"
echo
