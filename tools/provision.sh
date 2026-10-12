#!/usr/bin/env bash
# Provision the board over USB.  usage: WSN_WIFI_PASS=... tools/provision.sh --ssid S --bssid M --host H
# Password is read from WSN_WIFI_PASS or prompted; it is never written to disk.
set -euo pipefail
cd "$(dirname "$0")/.."
TTY_FLAG=()
[ -t 0 ] && TTY_FLAG=(-it)
exec docker run --rm "${TTY_FLAG[@]}" -e WSN_WIFI_PASS -u "$(id -u):$(id -g)" \
  --group-add "$(getent group dialout | cut -d: -f3)" \
  --device-cgroup-rule='c 166:* rmw' -v /dev:/dev \
  -v "$PWD/data":/data wsn-server python -m wsn.provision --port /dev/ttyACM0 "$@"
