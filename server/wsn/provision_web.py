"""Provision a new board with the Wi-Fi password typed on the /provision page (never written to disk).

The API keeps the password in memory for 10 minutes; this host-side process takes it once with the token from
data/run/provision.token, writes key / Wi-Fi / server / links into the board over USB and reports the result.

docker run --rm --network host --device /dev/ttyACM1 -v $PWD/data:/data wsn-server \
    python -m wsn.provision_web --port /dev/ttyACM1 --ssid HomeNet --bssid 02:00:00:00:a1:18 --host 192.168.4.77
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .provision import provision


def call(api: str, path: str, token: str, body: dict | None = None) -> dict | None:
    req = urllib.request.Request(api + path, json.dumps(body or {}).encode(),
                                 {"content-type": "application/json", "x-provision-token": token})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=5).read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    ap.add_argument("--ssid", required=True)
    ap.add_argument("--bssid", required=True)
    ap.add_argument("--host", required=True)
    ap.add_argument("--links", default="", help="comma separated transmitter MACs (same as the first board)")
    ap.add_argument("--api", default="http://127.0.0.1:8080")
    ap.add_argument("--data", default="/data")
    ap.add_argument("--minutes", type=float, default=15)
    a = ap.parse_args()
    token = (Path(a.data) / "run" / "provision.token").read_text().strip()
    end = time.monotonic() + a.minutes * 60
    print("waiting for the password from the /provision page ...", flush=True)
    while time.monotonic() < end:
        got = call(a.api, "/api/provision/take", token)
        if got:
            links = [m for m in a.links.split(",") if m]
            rc, msg = provision(a.port, a.ssid, a.bssid, a.host, got["password"], keys=str(Path(a.data) / "device_keys.json"),
                                links=links or None)
            del got
            call(a.api, "/api/provision/result", token, {"ok": rc == 0, "message": msg})
            print(msg, flush=True)
            return rc
        time.sleep(2)
    print("timeout: no password", flush=True)
    return 5


if __name__ == "__main__":
    sys.exit(main())
