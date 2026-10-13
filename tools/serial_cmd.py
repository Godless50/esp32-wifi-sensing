"""Send console commands to the board over USB and print the replies.

usage (inside wsn-server image): python serial_cmd.py [--wait S] "cmd1" ["sleep 10"] "cmd2" ...
"sleep N" pauses N seconds between commands. Opens the port without resetting the chip (DTR=1, RTS=0).
"""
import argparse
import sys
import time

import serial


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--wait", type=float, default=3.0, help="seconds to collect output after each command")
    ap.add_argument("cmds", nargs="+")
    a = ap.parse_args()
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = a.port, 115200, 0.2
    ser.dtr, ser.rts = True, False  # pyserial: выставить до open, иначе ESP32-S3 сбросится
    ser.open()
    time.sleep(0.3)
    ser.reset_input_buffer()
    for c in a.cmds:
        if c.startswith("sleep "):
            time.sleep(float(c.split()[1]))
            continue
        ser.write((c + "\r\n").encode())
        end = time.time() + a.wait
        buf = b""
        while time.time() < end:
            buf += ser.read(4096)
        sys.stdout.write(f">>> {c}\n" + buf.decode(errors="replace") + "\n")
        sys.stdout.flush()
    ser.close()


if __name__ == "__main__":
    main()
