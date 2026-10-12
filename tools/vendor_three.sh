#!/usr/bin/env bash
# Кладёт three.js в ui/vendor (UI работает без CDN). Версия зафиксирована.
set -euo pipefail
VER=0.169.0
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/ui/vendor"
mkdir -p "$OUT"
docker run --rm -v "$OUT":/out -w /tmp node:22-alpine sh -c "
  npm pack three@$VER >/dev/null 2>&1 && tar xzf three-$VER.tgz &&
  cp package/build/three.module.min.js /out/three.module.min.js &&
  cp package/examples/jsm/controls/OrbitControls.js /out/OrbitControls.js &&
  cp package/LICENSE /out/three.LICENSE &&
  chown $(id -u):$(id -g) /out/three.module.min.js /out/OrbitControls.js /out/three.LICENSE"
# OrbitControls импортирует 'three' — подменяем на локальный файл
sed -i "s#from 'three'#from './three.module.min.js'#" "$OUT/OrbitControls.js"
sha256sum "$OUT"/three.module.min.js "$OUT"/OrbitControls.js
