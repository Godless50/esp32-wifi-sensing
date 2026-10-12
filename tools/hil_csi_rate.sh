#!/usr/bin/env bash
# HIL check of server-side CSI rate for the board.
# usage: tools/hil_csi_rate.sh [seconds=30] [min_hz=80] [mac=<first non-test key in data/device_keys.json>]
set -euo pipefail
cd "$(dirname "$0")/.."
SECONDS_TOTAL="${1:-30}"
MIN_HZ="${2:-80}"
MAC="${3:-$(python3 -c 'import json; print(next(k for k in json.load(open("data/device_keys.json")) if not k.startswith("02:")))')}"
API="${WSN_API:-http://127.0.0.1:8080}"
python3 - "$SECONDS_TOTAL" "$MIN_HZ" "$MAC" "$API" <<'EOF'
import json, sys, time, urllib.request
total, min_hz, mac, api = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3], sys.argv[4]
samples = []
end = time.monotonic() + total
time.sleep(2)  # окно сервера 2 с
while time.monotonic() < end:
    try:
        body = json.load(urllib.request.urlopen(f"{api}/api/devices", timeout=2))
        dev = next((d for d in body["devices"] if d["device_id"] == mac), None)
        samples.append(dev["csi_hz"] if dev else 0.0)
    except Exception as e:
        print(f"poll error: {e}", file=sys.stderr)
        samples.append(0.0)
    time.sleep(1)
avg = sum(samples) / max(1, len(samples))
mn = min(samples) if samples else 0.0
ok = avg >= min_hz
srt = sorted(samples)
p10 = srt[len(srt) // 10] if srt else 0.0
low = sum(1 for s in samples if s < 50) / max(1, len(samples))
print(f"HIL mac={mac} samples={len(samples)} csi_hz={avg:.1f} min={mn:.1f} p10={p10:.1f} "
      f"below50={low * 100:.1f}% {'PASS' if ok else 'FAIL'}")
sys.exit(0 if ok else 1)
EOF
