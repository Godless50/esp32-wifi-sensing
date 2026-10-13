#!/bin/bash
# Ждёт пароль Wi-Fi со страницы /provision (до 15 мин) и прошивает настройки в плату по USB.
# usage: tools/provision_web.sh /dev/serial/by-id/<плата> [links]
# Пароль идёт из памяти API прямо в плату; в файлы, переменные окружения и журналы не попадает.
R=/home/ops/esp32-wifi-sensing
PORT=${1:?serial port}
LINKS=${2:-98:b3:ef:02:da:28,98:b3:ef:02:da:14,02:00:00:00:a1:2c,02:00:00:00:f6:7c,02:00:00:00:e5:57,02:00:00:00:b8:14,d8:d6:68:b3:7a:6f,02:00:00:00:a7:e6}
DEV=$(readlink -f "$PORT")
exec docker run --rm --network host -u "$(id -u):$(id -g)" --group-add "$(getent group dialout | cut -d: -f3)" \
  --device "$DEV" -v "$R/data":/data -v "$R/server":/app -w /app wsn-server \
  python -m wsn.provision_web --port "$DEV" --ssid HomeNet --bssid 02:00:00:00:a1:18 --host 192.168.4.77 --links "$LINKS"
