"""Provision boards over their USB console (install.sh runs this in the server image).

python -m wsn.provision scan --port /dev/ttyACM0            networks the board sees (JSON)
python -m wsn.provision --port P1 --port P2 --ssid S --host 192.168.1.10 --password-stdin
The access point is the strongest node of the network for each board unless --bssid is given.
Wi-Fi password: stdin (--password-stdin), env WSN_WIFI_PASS or an interactive prompt. It goes only into the
board's NVS; never to disk, logs or the command line.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

from .keys import KeyStore

_STATUS_RE = re.compile(r"^STATUS\s+(.*)$")
_AP_RE = re.compile(r"^AP\s+([0-9a-fA-F:]{17})\s+ch=(\d+)\s+rssi=(-?\d+)\s+ssid=(.*)$")


def parse_scan(lines) -> list[dict]:
    """Lines of the board's `scan` answer -> access points (bssid, ch, rssi, ssid)."""
    out = []
    for line in lines:
        m = _AP_RE.match(line.strip())
        if m:
            out.append({"bssid": m[1].lower(), "ch": int(m[2]), "rssi": int(m[3]), "ssid": m[4]})
        elif line.strip().startswith("AP_END"):
            break
    return out


def best_bssid(aps: list[dict], ssid: str) -> str | None:
    """Strongest access point of the network (with a mesh: the node nearest to this board)."""
    mine = [a for a in aps if a["ssid"] == ssid]
    return max(mine, key=lambda a: a["rssi"])["bssid"] if mine else None


def networks(aps: list[dict]) -> list[tuple[str, int]]:
    """Visible networks, strongest first: (ssid, best rssi)."""
    best: dict[str, int] = {}
    for a in aps:
        if a["ssid"]:
            best[a["ssid"]] = max(best.get(a["ssid"], -999), a["rssi"])
    return sorted(best.items(), key=lambda x: -x[1])


def console_quote(s: str) -> str:
    """Quote for esp_console_split_argv: double quotes, escape backslash and quote."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_status(line: str) -> dict[str, str] | None:
    m = _STATUS_RE.match(line.strip())
    if not m:
        return None
    return dict(kv.split("=", 1) for kv in m.group(1).split() if "=" in kv)


class Console:
    def __init__(self, port: str, timeout: float = 0.2):
        import serial  # pyserial

        self.ser = serial.Serial()
        self.ser.port, self.ser.baudrate, self.ser.timeout = port, 115200, timeout
        # pyserial выставляет DTR раньше RTS; DTR=0 при RTS=1 сбрасывает ESP32-S3 (USB-Serial-JTAG).
        # DTR=1, RTS=0 — рабочее состояние без сброса.
        self.ser.dtr = True
        self.ser.rts = False
        self.ser.open()

    def send(self, cmd: str) -> None:
        self.ser.write((cmd + "\r\n").encode())
        self.ser.flush()

    def lines(self, seconds: float):
        end = time.monotonic() + seconds
        buf = b""
        while time.monotonic() < end:
            buf += self.ser.read(256)
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                yield raw.decode(errors="replace").strip()

    def expect(self, needle: str, seconds: float) -> str | None:
        for line in self.lines(seconds):
            if needle in line:
                return line
        return None

    def scan(self, seconds: float = 10.0) -> list[dict]:
        self.send("scan")
        return parse_scan(self.lines(seconds))

    def close(self) -> None:
        self.ser.close()

    def status(self, seconds: float = 3.0) -> dict[str, str] | None:
        self.send("status")
        for line in self.lines(seconds):
            st = parse_status(line)
            if st is not None:
                return st
        return None


def wait_status(con, attempts: int = 6, pause: float = 1.0) -> dict[str, str] | None:
    """Opening the USB-Serial-JTAG port may reset the chip: retry until it answers."""
    for _ in range(attempts):
        st = con.status()
        if st is not None:
            return st
        time.sleep(pause)
    return None


def provision(port: str, ssid: str, bssid: str, host: str, password: str, udp_port: int = 5005,
              keys: str = "/data/device_keys.json", wait: float = 40.0, links: list[str] | None = None) -> tuple[int, str]:
    """Key, Wi-Fi, server (and optional extra links) into the board's NVS; returns (exit code, message)."""
    con = Console(port)
    con.send("")
    st = wait_status(con)
    if st is None or "mac" not in st:
        return 2, "no STATUS from board (is the firmware flashed?)"
    mac = st["mac"].lower()
    ks = KeyStore(Path(keys))
    # Ключ меняется при каждом провижининге: сервер по смене ключа сбрасывает счётчик загрузок
    # устройства (нужно после стирания флеша, когда boot_id на плате снова начинается с 1).
    key = secrets.token_bytes(32)
    ks.set(mac, key)
    steps = [
        ("key set " + key.hex(), "OK key"),
        (f"wifi set {console_quote(ssid)} {console_quote(password)} {bssid.lower()}", "OK wifi"),
        (f"server set {console_quote(host)} {udp_port}", "OK server"),
    ]
    if links:
        steps.append(("links set " + ",".join(links), "OK links"))
    for cmd, ok in steps:
        con.send(cmd)
        if con.expect(ok, 3.0) is None:
            shown = cmd.split()[0] + " " + cmd.split()[1]  # не печатать пароль/ключ
            return 3, f"board did not confirm '{shown}'"
    con.send("reboot")
    time.sleep(3)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        st = con.status()
        if st and st.get("wifi") == "CONNECTED":
            return 0, f"{mac}: wifi CONNECTED, ip {st.get('ip', '?')}"
        time.sleep(2)
    return 4, f"{mac}: board did not reach wifi=CONNECTED (wrong password?)"


def scan_port(port: str) -> list[dict]:
    con = Console(port)
    try:
        con.send("")
        if wait_status(con) is None:
            return []
        return con.scan()
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Wi-Fi, server address and key into boards over USB")
    ap.add_argument("cmd", nargs="?", choices=("set", "scan"), default="set",
                    help="set: provision (default); scan: print the networks the board sees (JSON)")
    ap.add_argument("--port", action="append", help="serial port; repeat for several boards (default /dev/ttyACM0)")
    ap.add_argument("--ssid")
    ap.add_argument("--bssid", help="access point; default: the strongest node of the network for each board")
    ap.add_argument("--host")
    ap.add_argument("--udp-port", type=int, default=5005)
    ap.add_argument("--keys", default="/data/device_keys.json")
    ap.add_argument("--wait", type=float, default=40.0, help="seconds to wait for wifi=CONNECTED")
    ap.add_argument("--password-stdin", action="store_true", help="read the Wi-Fi password from the first line of stdin")
    a = ap.parse_args(argv)
    ports = a.port or ["/dev/ttyACM0"]
    if a.cmd == "scan":
        aps = [x for p in ports for x in scan_port(p)]
        print(json.dumps({"aps": aps, "networks": networks(aps)}, ensure_ascii=False))
        return 0 if aps else 2
    if not a.ssid or not a.host:
        ap.error("set needs --ssid and --host")
    if a.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = os.environ.get("WSN_WIFI_PASS") or getpass.getpass("Wi-Fi password: ")
    worst = 0
    for port in ports:
        bssid = a.bssid
        if not bssid:
            bssid = best_bssid(scan_port(port), a.ssid)
            if not bssid:
                print(f"ERROR: {port}: the board does not see the network {a.ssid!r}", file=sys.stderr, flush=True)
                worst = max(worst, 5)
                continue
        rc, msg = provision(port, a.ssid, bssid, a.host, password, a.udp_port, a.keys, a.wait)
        print(("ERROR: " if rc else "OK ") + msg + ("" if rc else f" (access point {bssid})"),
              file=sys.stderr if rc else sys.stdout, flush=True)
        worst = max(worst, rc)
    del password
    return worst


if __name__ == "__main__":
    sys.exit(main())
