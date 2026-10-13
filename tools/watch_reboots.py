"""Следить за перезапусками плат по телеметрии API: падение uptime = перезапуск. Пишет строки в лог.

usage: python3 tools/watch_reboots.py <seconds> <out.log>
"""
import json
import sys
import time
import urllib.request


def main(sec: float, out: str) -> None:
    last: dict[str, tuple[float, float]] = {}
    end = time.time() + sec
    with open(out, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} start\n")
        while time.time() < end:
            try:
                with urllib.request.urlopen("http://127.0.0.1:8080/api/devices", timeout=2) as r:
                    devs = json.loads(r.read())["devices"]
            except (OSError, ValueError):
                time.sleep(2)
                continue
            now = time.time()
            for d in devs:
                dev, t = d["device_id"], d.get("telemetry") or {}
                up, age = t.get("uptime_s"), d.get("age_s", 0)
                if up is None:
                    continue
                prev = last.get(dev)
                if prev is not None and up + 5 < prev[0] + (now - prev[1]):
                    f.write(f"{time.strftime('%H:%M:%S')} REBOOT {dev} uptime {prev[0] + (now - prev[1]):.0f} -> {up}"
                            f" rssi {t.get('rssi_avg')} heap {t.get('heap_free')} age {age:.1f}\n")
                    f.flush()
                last[dev] = (up, now)
            time.sleep(2)
        f.write(f"{time.strftime('%H:%M:%S')} end\n")


if __name__ == "__main__":
    main(float(sys.argv[1]), sys.argv[2])
