#!/usr/bin/env bash
# Create UI password (once) and data/caddy.env for Caddy.  usage: tools/ui_password.sh [host-ip]
# Re-running keeps the existing password; the host IP can be changed by passing it.
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
mkdir -p data
HOST="${1:-$(grep -s '^WSN_HOST=' data/caddy.env | cut -d= -f2 || true)}"
HOST="${HOST:-$(hostname -I | awk '{print $1}')}"
if [ ! -s data/ui_password ]; then
  tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 20 > data/ui_password
  echo "new UI password generated: data/ui_password"
fi
HASH=$(docker run --rm caddy:2-alpine caddy hash-password --plaintext "$(cat data/ui_password)")
printf "WSN_HOST=%s\nWSN_UI_HASH='%s'\n" "$HOST" "$HASH" > data/caddy.env
echo "UI: https://$HOST:8443  user: wsn  password: data/ui_password"
