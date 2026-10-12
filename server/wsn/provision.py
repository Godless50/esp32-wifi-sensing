"""Provision the board over its USB console.

python -m wsn.provision --port /dev/ttyACM0 --ssid HomeNet --bssid 02:00:00:00:a1:18 --host 192.168.4.77
Wi-Fi password: env WSN_WIFI_PASS or interactive prompt. Never written to disk.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import secrets
import sys
import time
from pathlib import Path

from .keys import KeyStore

_STATUS_RE = re.compile(r"^STATUS\s+(.*)$")


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--ssid", required=True)
    ap.add_argument("--bssid", required=True)
    ap.add_argument("--host", required=True)
    ap.add_argument("--udp-port", type=int, default=5005)
    ap.add_argument("--keys", default="/data/device_keys.json")
    ap.add_argument("--wait", type=float, default=40.0, help="seconds to wait for wifi=CONNECTED")
    a = ap.parse_args()
    password = os.environ.get("WSN_WIFI_PASS") or getpass.getpass("Wi-Fi password: ")

    con = Console(a.port)
    con.send("")
    st = wait_status(con)
    if st is None or "mac" not in st:
        print("ERROR: no STATUS from board (is the firmware flashed?)", file=sys.stderr)
        return 2
    mac = st["mac"].lower()
    ks = KeyStore(Path(a.keys))
    key = ks.get(mac)
    if key is None:
        key = secrets.token_bytes(32)
        ks.set(mac, key)
        print(f"new key generated for {mac}")
    steps = [
        ("key set " + key.hex(), "OK key"),
        (f"wifi set {console_quote(a.ssid)} {console_quote(password)} {a.bssid.lower()}", "OK wifi"),
        (f"server set {console_quote(a.host)} {a.udp_port}", "OK server"),
    ]
    for cmd, ok in steps:
        con.send(cmd)
        if con.expect(ok, 3.0) is None:
            shown = cmd.split()[0] + " " + cmd.split()[1]  # не печатать пароль/ключ
            print(f"ERROR: board did not confirm '{shown}'", file=sys.stderr)
            return 3
    con.send("reboot")
    time.sleep(3)
    deadline = time.monotonic() + a.wait
    while time.monotonic() < deadline:
        st = con.status()
        if st:
            print("STATUS", " ".join(f"{k}={v}" for k, v in st.items()))
            if st.get("wifi") == "CONNECTED":
                return 0
        time.sleep(2)
    print("ERROR: board did not reach wifi=CONNECTED", file=sys.stderr)
    return 4


if __name__ == "__main__":
    sys.exit(main())
