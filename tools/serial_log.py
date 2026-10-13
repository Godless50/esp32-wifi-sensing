"""Писать консоль платы в файл с метками времени, не сбрасывая её (DTR=1, RTS=0). Ловит паники с бэктрейсом.

usage: python3 tools/serial_log.py <port> <seconds> <out.log>
"""
import sys
import time

import serial


def main(port: str, sec: float, out: str) -> None:
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = port, 115200, 0.5
    s.dtr, s.rts = True, False
    s.open()
    end = time.time() + sec
    buf = b""
    with open(out, "a", encoding="utf-8") as f:
        while time.time() < end:
            try:
                buf += s.read(4096)
            except serial.SerialException:
                f.write(f"{time.strftime('%H:%M:%S')} [port lost]\n")
                f.flush()
                time.sleep(2)
                try:
                    s.close()
                    s.open()
                except serial.SerialException:
                    pass
                continue
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode(errors="replace").rstrip()
                if " infer: collect " in text:  # каждые 250 мс, не нужно
                    continue
                f.write(f"{time.strftime('%H:%M:%S')} {text}\n")
            f.flush()


if __name__ == "__main__":
    main(sys.argv[1], float(sys.argv[2]), sys.argv[3])
