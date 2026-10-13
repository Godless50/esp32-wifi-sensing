"""Раз в секунду: CSI Гц каждой платы на сервере и по её телеметрии, в CSV. Ищем общие для всех плат провалы.

usage: python3 tools/rate_log.py <seconds> <out.csv>
"""
import json
import sys
import time
import urllib.request

dur, out = float(sys.argv[1]), sys.argv[2]
end = time.time() + dur
with open(out, "a") as f:
    while time.time() < end:
        t0 = time.time()
        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/api/devices", timeout=2) as r:
                d = json.load(r)["devices"]
            row = [time.strftime("%H:%M:%S")]
            for x in sorted(d, key=lambda x: x["device_id"]):
                t = x.get("telemetry") or {}
                links = {k[-5:]: v["hz"] for k, v in x["links"].items()}
                row.append(f"{x['device_id'][-5:]}:{x['csi_hz']:.0f}:{t.get('csi_hz', 0):.0f}:{t.get('uptime_s')}:"
                           + "/".join(f"{k}={v:.0f}" for k, v in sorted(links.items()) if v > 0))
            f.write(",".join(row) + "\n")
            f.flush()
        except Exception as e:
            f.write(f"{time.strftime('%H:%M:%S')},ERR {e}\n")
        time.sleep(max(0.0, 1.0 - (time.time() - t0)))
