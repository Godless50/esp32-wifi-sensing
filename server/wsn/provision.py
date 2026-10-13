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
    rc, msg = provision(a.port, a.ssid, a.bssid, a.host, password, a.udp_port, a.keys, a.wait)
    print(("ERROR: " if rc else "") + msg, file=sys.stderr if rc else sys.stdout)
    return rc


if __name__ == "__main__":
    sys.exit(main())
