#!/usr/bin/env bash
# WSN — установка «под ключ»: сервер, прошивка плат ESP32-S3, их настройка, сопряжение телефона.
#
#   ./install.sh          всё по порядку; повторный запуск безопасен (готовое пропускается)
#   ./install.sh flash    только прошить и настроить платы, подключённые к этому компьютеру по USB
#   ./install.sh wifi     только настроить Wi-Fi уже прошитых плат (без прошивки)
#   ./install.sh pair     показать QR-код для приложения ещё раз
#   ./install.sh status   что работает сейчас
#   ./install.sh password сменить пароль приложения и веб-интерфейса
#   ./install.sh apk      собрать приложение из исходников (если нет готового)
# Ключи:  --yes  не спрашивать подтверждения прошивки;  --host IP  адрес этого компьютера в домашней сети.
#
# Пароль Wi-Fi спрашивается один раз и уходит только в память плат (NVS): на диск, в журналы и в командную
# строку он не попадает. Пароль приложения хранится только хешем (data/caddy.env).
set -euo pipefail
cd "$(dirname "$0")"
ROOT=$PWD
DATA=$ROOT/data
DIST=$ROOT/firmware/dist
REPO=Godless50/esp32-wifi-sensing
IMG=wsn-server
COMPOSE=(docker compose -f "$ROOT/deploy/docker-compose.yml")
YES=0
HOST_ARG=""
CMD=all

while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) YES=1 ;;
    --host) HOST_ARG=${2:?нужен адрес}; shift ;;
    all|flash|wifi|pair|status|password|apk) CMD=$1 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "неизвестный аргумент: $1 (./install.sh --help)" >&2; exit 1 ;;
  esac
  shift
done

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die() { printf '\n\033[31mОшибка: %s\033[0m\n' "$*" >&2; exit 1; }
ask() { local a; read -r -p "  $1${2:+ [$2]}: " a </dev/tty; printf '%s' "${a:-${2:-}}"; }
confirm() { [ "$YES" = 1 ] && return 0; local a; read -r -p "  $1 [Д/н]: " a </dev/tty; [[ -z $a || $a =~ ^[ДдYy] ]]; }

# ---------------------------------------------------------------- окружение
check_prereq() {
  bold "1. Проверка компьютера"
  [ "$(uname -s)" = Linux ] || die "нужен Linux (Ubuntu, Debian, Fedora…): плате нужен доступ по USB из Docker"
  command -v docker >/dev/null || die "нет Docker. Установка: https://docs.docker.com/engine/install/"
  docker info >/dev/null 2>&1 || die "нет доступа к Docker. Выполните: sudo usermod -aG docker \$USER — и войдите в систему заново"
  docker compose version >/dev/null 2>&1 || die "нужен Docker Compose v2 (пакет docker-compose-plugin)"
  ok "Docker $(docker version -f '{{.Server.Version}}' 2>/dev/null)"
  if systemctl is-active --quiet ModemManager 2>/dev/null && [ ! -e /etc/udev/rules.d/99-esp32-no-modemmanager.rules ]; then
    warn "ModemManager может перехватывать порт платы и сбрасывать её. Один раз выполните:"
    warn "  sudo cp tools/99-esp32-no-modemmanager.rules /etc/udev/rules.d/ && sudo udevadm control --reload"
  fi
  mkdir -p "$DATA" "$ROOT/ui/download"
  local tz
  tz=$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo UTC)
  # переменные для docker compose: файлы в data/ — от имени пользователя, а не root
  printf 'WSN_UID=%s\nWSN_GID=%s\nWSN_TZ=%s\n' "$(id -u)" "$(id -g)" "${tz:-UTC}" > "$ROOT/deploy/.env"
}

detect_host() {
  local saved now
  saved=$(grep -s '^WSN_HOST=' "$DATA/caddy.env" | cut -d= -f2 || true)
  now=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") {print $(i + 1); exit}}')
  HOST=${HOST_ARG:-${saved:-$now}}
  [ -n "$HOST" ] || HOST=$(ask "Адрес этого компьютера в домашней сети (IP)")
  [ -n "$HOST" ] || die "не задан адрес компьютера"
  if [ -n "$saved" ] && [ -n "$now" ] && [ "$saved" != "$now" ] && [ -z "$HOST_ARG" ]; then
    warn "раньше компьютер был $saved, сейчас $now. Платы шлют данные на $saved."
    warn "Закрепите адрес за компьютером в роутере или перенастройте платы: ./install.sh --host $now wifi"
  fi
}

api() {  # GET к серверу изнутри его контейнера (на компьютере не нужен curl)
  "${COMPOSE[@]}" exec -T api python -c \
    "import sys, urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080' + sys.argv[1], timeout=5).read().decode())" "$1"
}

set_password() {
  local p1 p2
  bold "Пароль для приложения и веб-интерфейса (пользователь wsn)"
  while :; do
    read -r -s -p "  Новый пароль (не короче 8 символов): " p1 </dev/tty; echo
    read -r -s -p "  Ещё раз: " p2 </dev/tty; echo
    [ "$p1" = "$p2" ] || { warn "не совпадают"; continue; }
    [ ${#p1} -ge 8 ] || { warn "слишком короткий"; continue; }
    break
  done
  local hash
  hash=$(printf '%s\n' "$p1" | docker run -i --rm caddy:2-alpine caddy hash-password) || die "не удалось получить хеш пароля"
  unset p1 p2
  (umask 077; printf "WSN_HOST=%s\nWSN_UI_HASH='%s'\n" "$HOST" "$hash" > "$DATA/caddy.env")
  ok "пароль сохранён (только хеш, data/caddy.env)"
}

ensure_caddy_env() {
  if grep -qs '^WSN_UI_HASH=' "$DATA/caddy.env"; then
    local hash
    hash=$(grep '^WSN_UI_HASH=' "$DATA/caddy.env" | cut -d= -f2-)
    (umask 077; printf "WSN_HOST=%s\nWSN_UI_HASH=%s\n" "$HOST" "$hash" > "$DATA/caddy.env")
  else
    set_password
  fi
}

start_server() {
  bold "2. Сервер"
  detect_host
  ensure_caddy_env
  "${COMPOSE[@]}" up -d --build --remove-orphans >/dev/null 2>&1 || "${COMPOSE[@]}" up -d --build --remove-orphans
  local i
  for i in $(seq 60); do api /api/status >/dev/null 2>&1 && break; sleep 1; done
  api /api/status >/dev/null 2>&1 || die "сервер не отвечает: ${COMPOSE[*]} logs api"
  ok "сервер работает: https://$HOST:8443 (пользователь wsn)"
}

# ---------------------------------------------------------------- платы
board_ports() {  # платы ESP32-S3 на USB (встроенный USB-Serial/JTAG): постоянные имена, номер ttyACM может меняться
  local p
  for p in /dev/serial/by-id/*Espressif*; do [ -e "$p" ] && echo "$p"; done
}

in_image() {  # команда в образе сервера с доступом к портам плат: in_image "<порт> [порт…]" cmd...
  local args=() p dev
  for p in $1; do
    dev=$(readlink -f "$p")
    args+=(--device "$dev" --group-add "$(stat -c %g "$dev")")
  done
  shift
  docker run --rm -i -u "$(id -u):$(id -g)" -e HOME=/tmp "${args[@]}" -v "$DATA":/data -v "$DIST":/fw:ro "$IMG" "$@"
}

esptool() {  # тот же путь устройства внутри контейнера: esptool узнаёт USB-Serial/JTAG по нему (сброс в загрузчик)
  local dev=$1; shift
  in_image "$dev" python -m esptool --chip esp32s3 -p "$(readlink -f "$dev")" "$@"
}

flash_one() {
  local dev=$1 info mac psram flash variant
  info=$(esptool "$dev" --before default_reset --after watchdog_reset flash_id 2>&1) || {
    warn "$dev: плата не отвечает загрузчику. Зажмите BOOT, нажмите и отпустите RESET, отпустите BOOT — и повторите"
    return 1; }
  mac=$(sed -n 's/^MAC: *//p' <<<"$info" | head -1)
  flash=$(sed -n 's/^Detected flash size: *\([0-9]*\)MB.*/\1/p' <<<"$info" | head -1)
  psram=no
  grep -q 'PSRAM' <<<"$(grep '^Features:' <<<"$info")" && psram=yes
  if [ "$psram" = yes ] && [ "${flash:-0}" -ge 16 ]; then variant=main
  elif [ "${flash:-0}" -ge 8 ]; then variant=lite
  else warn "$dev ($mac): флеш ${flash:-?} МБ — нужна ESP32-S3 с флешем от 8 МБ"; return 1; fi
  [ -f "$DIST/wsn-$variant.bin" ] || die "нет образа прошивки firmware/dist/wsn-$variant.bin"
  echo "  плата $mac: флеш ${flash} МБ, PSRAM: $psram -> прошивка $variant"
  confirm "Прошить плату $mac? Всё, что на ней было, будет стёрто" || return 1
  sleep 2  # плата перезапустилась после опроса
  esptool "$dev" --before default_reset --after no_reset -b 460800 write_flash --erase-all 0x0 "/fw/wsn-$variant.bin" \
    > "$DATA/run/flash_${mac//:/}.log" 2>&1 || { warn "$mac: прошивка не удалась (data/run/flash_${mac//:/}.log)"; return 1; }
  # сброс по RTS у части плат оставляет чип в загрузчике; сброс сторожевым таймером запускает прошивку
  esptool "$dev" --before no_reset --after watchdog_reset chip_id >/dev/null 2>&1 || true
  ok "$mac прошита ($variant)"
}

flash_boards() {
  bold "3. Прошивка плат"
  mkdir -p "$DATA/run"
  local ports
  ports=$(board_ports)
  if [ -z "$ports" ]; then
    warn "платы не найдены. Подключите ESP32-S3 кабелем с данными к разъёму USB (не UART) и запустите: ./install.sh flash"
    return 0
  fi
  docker image inspect "$IMG" >/dev/null 2>&1 || "${COMPOSE[@]}" build api >/dev/null
  local dev n=0
  for dev in $ports; do flash_one "$dev" && n=$((n + 1)) || true; done
  [ "$n" -gt 0 ] && { sleep 6; ok "прошито плат: $n"; }
  setup_wifi
}

setup_wifi() {
  bold "4. Wi-Fi и адрес сервера в платы"
  local ports
  ports=$(board_ports)
  [ -n "$ports" ] || { warn "платы не подключены по USB — пропуск"; return 0; }
  local first scan nets ssid i pw rc
  first=$(head -1 <<<"$ports")
  sleep 3
  scan=$(in_image "$first" python -m wsn.provision scan --port "$(readlink -f "$first")" 2>/dev/null || true)
  nets=$(cli nets <<<"$scan" 2>/dev/null || true)
  if [ -n "$nets" ]; then
    echo "  Сети, которые видит плата:"
    i=0
    while IFS=$'\t' read -r name rssi; do i=$((i + 1)); printf '   %2d) %s  (%s дБм)\n' "$i" "$name" "$rssi"; done <<<"$nets"
    local pick
    pick=$(ask "Номер сети или её имя" 1)
    if [[ $pick =~ ^[0-9]+$ ]] && [ "$pick" -ge 1 ] && [ "$pick" -le "$i" ]; then
      ssid=$(sed -n "${pick}p" <<<"$nets" | cut -f1)
    else
      ssid=$pick
    fi
  else
    ssid=$(ask "Имя сети Wi-Fi (2,4 ГГц)")
  fi
  [ -n "$ssid" ] || die "не выбрана сеть"
  read -r -s -p "  Пароль Wi-Fi «$ssid» (уйдёт только в платы): " pw </dev/tty; echo
  local args=()
  for i in $ports; do args+=(--port "$(readlink -f "$i")"); done
  set +e
  printf '%s\n' "$pw" | in_image "$ports" python -m wsn.provision "${args[@]}" --ssid "$ssid" --host "$HOST" \
    --keys /data/device_keys.json --password-stdin
  rc=$?
  set -e
  unset pw
  [ $rc -eq 0 ] || warn "не все платы подключились к Wi-Fi (неверный пароль? сеть 5 ГГц?) — повторите: ./install.sh wifi"
  wait_boards
}

cli() { "${COMPOSE[@]}" exec -T api python -m wsn.cli "$@"; }

wait_boards() {
  local i want
  want=$(board_ports | wc -l)
  echo "  Жду данные плат на сервере…"
  for i in $(seq 45); do
    [ "$(cli boards 2>/dev/null || echo 0)" -ge "$want" ] && break
    sleep 2
  done
  cli status || true
}

# ---------------------------------------------------------------- телефон
fetch_apk() {
  bold "5. Приложение для телефона"
  local dst=$ROOT/ui/download/wsn.apk tmp
  tmp=$(mktemp -d)
  if command -v gh >/dev/null && gh release download --repo "$REPO" --pattern wsn.apk --dir "$tmp" --clobber >/dev/null 2>&1; then
    mv "$tmp/wsn.apk" "$dst"
  elif curl -fsSL -o "$tmp/wsn.apk" "https://github.com/$REPO/releases/latest/download/wsn.apk" 2>/dev/null; then
    mv "$tmp/wsn.apk" "$dst"
  fi
  rm -rf "$tmp"
  if [ -s "$dst" ]; then
    ok "приложение: $dst (телефон скачает его по QR-коду ниже)"
  else
    warn "готовое приложение не скачалось (репозиторий закрыт? нет сети?): https://github.com/$REPO/releases"
    warn "собрать из исходников (Docker, ~10 мин): ./install.sh apk"
  fi
}

build_apk() {
  bold "Сборка приложения из исходников"
  "$ROOT/tools/android_build.sh" release
  cp "$ROOT/android/app/build/outputs/apk/release/wsn.apk" "$ROOT/ui/download/wsn.apk"
  ok "ui/download/wsn.apk"
}

pair() {
  bold "6. Сопряжение телефона"
  local pem i
  for i in $(seq 30); do
    pem=$("${COMPOSE[@]}" exec -T caddy cat /data/caddy/pki/authorities/local/root.crt 2>/dev/null || true)
    grep -q "BEGIN CERTIFICATE" <<<"$pem" && break
    curl -sk -o /dev/null "https://$HOST:8443/" 2>/dev/null || true  # Caddy выпускает сертификат при первом запросе
    sleep 1
  done
  grep -q "BEGIN CERTIFICATE" <<<"$pem" || die "Caddy не выдал сертификат: ${COMPOSE[*]} logs caddy"
  local apk=()
  [ -s "$ROOT/ui/download/wsn.apk" ] && apk=(--apk)
  "${COMPOSE[@]}" exec -T api python -m wsn.pairing --host "$HOST" "${apk[@]}" <<<"$pem"
}

next_steps() {
  bold "Дальше — в приложении на телефоне (Android с поддержкой ARCore)"
  cat <<EOF
  1. Установите приложение (QR-код со ссылкой выше) и отсканируйте QR-код сопряжения; пароль — тот, что задан здесь.
  2. «Разметка и запись»: отметьте каждую плату и точку доступа, обойдите углы комнат, затем пройдите сценарий
     и нажмите «Сохранить». В квартире во время записи — только вы.
  3. На этом компьютере: ./train.sh — обучение модели под вашу квартиру (несколько минут).
  4. В приложении «Карта» — комнаты, где есть движение, и где вы сейчас. Веб-интерфейс: https://$HOST:8443
EOF
}

# ---------------------------------------------------------------- главное
case "$CMD" in
  all) check_prereq; start_server; flash_boards; fetch_apk; pair; next_steps ;;
  flash) check_prereq; start_server; flash_boards ;;
  wifi) check_prereq; start_server; setup_wifi ;;
  pair) check_prereq; detect_host; pair ;;
  status) check_prereq; detect_host; cli status ;;
  password) check_prereq; detect_host; set_password; "${COMPOSE[@]}" up -d --force-recreate caddy >/dev/null; ok "пароль сменён" ;;
  apk) check_prereq; build_apk ;;
esac
