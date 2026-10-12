#!/usr/bin/env bash
# Build / test / install the Android capture app in Docker.
#   tools/android_build.sh test               JVM unit tests
#   tools/android_build.sh build              assembleDebug (copies Caddy root CA into the app first)
#   tools/android_build.sh install [serial]   build + adb install -r (default: first device)
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
CMD="${1:-build}"
mkdir -p "$ROOT/.gradle-cache"
run() {
  docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -e GRADLE_USER_HOME=/cache \
    -v "$ROOT/android":/project -v "$ROOT/.gradle-cache":/cache wsn-android-build "$@"
}
copy_ca() {
  local dst="$ROOT/android/app/src/main/res/raw/wsn_ca.pem"
  docker run --rm -v wsn_caddy_data:/data:ro --entrypoint cat caddy:2-alpine \
    /data/caddy/pki/authorities/local/root.crt > "$dst.tmp"
  grep -q "BEGIN CERTIFICATE" "$dst.tmp" && mv "$dst.tmp" "$dst"
}
case "$CMD" in
  test) copy_ca; run gradle --no-daemon -q testDebugUnitTest ;;
  build) copy_ca; run gradle --no-daemon -q assembleDebug; ls -l android/app/build/outputs/apk/debug/app-debug.apk ;;
  install)
    copy_ca; run gradle --no-daemon -q assembleDebug
    SERIAL="${2:-$(adb devices | awk 'NR>1 && $2=="device"{print $1; exit}')}"
    adb -s "$SERIAL" install -r android/app/build/outputs/apk/debug/app-debug.apk ;;
  *) echo "usage: $0 test|build|install [serial]" >&2; exit 1 ;;
esac
