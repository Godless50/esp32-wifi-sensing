#!/usr/bin/env bash
# Сборка приложения для телефона в Docker (JDK 17 + Android SDK 35, ничего ставить не нужно).
#   tools/android_build.sh test               юнит-тесты
#   tools/android_build.sh build              отладочный APK
#   tools/android_build.sh release            APK для раздачи: android/app/build/outputs/apk/release/wsn.apk
#   tools/android_build.sh install [serial]   release + adb install -r на подключённый телефон
# Ключ подписи релиза создаётся один раз в ~/.config/wsn/release.jks (пароль рядом, только для владельца) и в
# репозиторий не попадает. Один ключ — обновления приложения ставятся поверх без удаления.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
CMD="${1:-build}"
KEYDIR="$HOME/.config/wsn"
mkdir -p "$ROOT/.gradle-cache"
docker image inspect wsn-android-build >/dev/null 2>&1 \
  || docker build -t wsn-android-build -f "$ROOT/deploy/Dockerfile.android" "$ROOT/deploy"
run() {
  # HOME в постоянном томе: там ~/.android/debug.keystore — иначе каждая отладочная сборка с новым ключом
  local keys=()
  [ -d "$KEYDIR" ] && keys=(-e WSN_KEYSTORE -e WSN_KEYSTORE_PASS -v "$KEYDIR":/keys)  # папку не создаёт Docker (root)
  docker run --rm -u "$(id -u):$(id -g)" -e HOME=/cache/home -e GRADLE_USER_HOME=/cache "${keys[@]}" \
    -v "$ROOT/android":/project -v "$ROOT/.gradle-cache":/cache wsn-android-build "$@"
}
release_key() {
  mkdir -p "$KEYDIR" && chmod 700 "$KEYDIR"
  if [ ! -s "$KEYDIR/release.jks" ]; then
    (umask 077; od -An -N18 -tx1 /dev/urandom | tr -d ' \n' > "$KEYDIR/release.pass")
    WSN_KEYSTORE_PASS=$(cat "$KEYDIR/release.pass") run sh -c 'keytool -genkeypair -keystore /keys/release.jks \
      -alias wsn -keyalg RSA -keysize 3072 -validity 10000 -storepass "$WSN_KEYSTORE_PASS" -keypass "$WSN_KEYSTORE_PASS" \
      -dname "CN=WSN, O=WSN"' >/dev/null
    echo "ключ подписи создан: $KEYDIR/release.jks"
  fi
  export WSN_KEYSTORE=/keys/release.jks
  WSN_KEYSTORE_PASS=$(cat "$KEYDIR/release.pass")
  export WSN_KEYSTORE_PASS
}
release() {
  release_key
  run gradle --no-daemon -q assembleRelease
  cp android/app/build/outputs/apk/release/app-release.apk android/app/build/outputs/apk/release/wsn.apk
  ls -l android/app/build/outputs/apk/release/wsn.apk
}
case "$CMD" in
  test) run gradle --no-daemon -q testDebugUnitTest ;;
  build) run gradle --no-daemon -q assembleDebug; ls -l android/app/build/outputs/apk/debug/app-debug.apk ;;
  release) release ;;
  install)
    release
    SERIAL="${2:-$(adb devices | awk 'NR>1 && $2=="device"{print $1; exit}')}"
    adb -s "$SERIAL" install -r android/app/build/outputs/apk/release/wsn.apk ;;
  *) echo "usage: $0 test|build|release|install [serial]" >&2; exit 1 ;;
esac
