#!/usr/bin/env bash
# Готовые образы прошивки для install.sh: firmware/dist/wsn-main.bin и wsn-lite.bin — загрузчик, таблица разделов,
# otadata и приложение одним файлом с адреса 0x0 (install.sh пишет его с полным стиранием флеша).
#   main — ESP32-S3 с 16 МБ флеша и PSRAM (модули N16R8);  lite — 8 МБ флеша и больше, без PSRAM.
# usage: tools/release_fw.sh [--no-build]     (--no-build: склеить уже собранные firmware/build*)
set -euo pipefail
cd "$(dirname "$0")/.."
R=$PWD
IMG=wsn-server
[ "${1:-}" = "--no-build" ] || { tools/fw.sh build; tools/fw.sh build-lite; }
docker image inspect "$IMG" >/dev/null 2>&1 || docker compose -f deploy/docker-compose.yml build api
mkdir -p firmware/dist
for v in main lite; do
  b=build; [ "$v" = lite ] && b=build_lite
  docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$R/firmware":/fw -w "/fw/$b" "$IMG" \
    python -m esptool --chip esp32s3 merge_bin -o "/fw/dist/wsn-$v.bin" "@flash_args" >/dev/null
done
SHA=$(git rev-parse --short HEAD)$(git diff --quiet -- firmware || echo -dirty)
python3 - "$SHA" <<'EOF'
import hashlib, json, sys, time
from pathlib import Path
fw = Path("firmware")
out = {"git": sys.argv[1], "built": time.strftime("%Y-%m-%d %H:%M:%S"), "images": {}}
for v, build, flash, psram in (("main", "build", "16MB", True), ("lite", "build_lite", "8MB", False)):
    app = (fw / build / "wsn_fw.bin").read_bytes()
    assert app[0] == 0xE9 and app[0x20:0x24] == bytes.fromhex("3254cdab"), "not an ESP app image"
    s = app[0x20 + 144:0x20 + 147]
    merged = (fw / "dist" / f"wsn-{v}.bin").read_bytes()
    out["images"][v] = {"file": f"wsn-{v}.bin", "flash": flash, "psram": psram, "bytes": len(merged),
                        "sha256": hashlib.sha256(merged).hexdigest(),
                        "fw_id": "%08x" % (0x80000000 | (s[0] << 16) | (s[1] << 8) | s[2])}
(fw / "dist" / "manifest.json").write_text(json.dumps(out, indent=1) + "\n")
print(json.dumps(out, indent=1))
EOF
