"""Измерение потока плат: частота CSI, интервалы между кадрами (p50/p95/p99), провалы, потери датаграмм,
время инференса на плате, память. Числа с устройства, а не «на глаз»: база для сравнения до/после изменений.

Источник — копия всех датаграмм от API (data/run/measure.sock, см. wsn/__main__.py); живой learner
(data/run/csi.sock) не трогается. Пока скрипт не запущен, сокета нет и API ничего не шлёт.

usage: PYTHONPATH=server python3 tools/link_metrics.py <seconds> [--out data/eval/metrics-<time>.json] [--tag text]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import socket
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

from wsn.keys import KeyStore
from wsn.protocol import (TYPE_CSI, TYPE_CSI_SRC, TYPE_PRED, TYPE_TELEM, ProtocolError, parse_packet,
                          peek_device_id)

ROOT = Path(__file__).resolve().parent.parent
GAPS_MS = (50, 200, 1000)


def pct(xs: list[float], q: float) -> float | None:
    """Перцентиль по методу ближайшего ранга; None для пустого списка."""
    if not xs:
        return None
    s = sorted(xs)
    k = max(0, min(len(s) - 1, math.ceil(q / 100.0 * len(s)) - 1))
    return s[k]


def link_stats(t_us: list[int], seconds: float) -> dict:
    """Интервалы по часам платы (t_us), а не по времени прихода: сеть не размазывает картину."""
    t = sorted(t_us)
    dt = [(b - a) / 1000.0 for a, b in zip(t, t[1:]) if b > a]
    out = {"frames": len(t), "hz": round(len(t) / seconds, 1) if seconds > 0 else 0.0}
    for q in (50, 95, 99):
        v = pct(dt, q)
        out[f"dt_ms_p{q}"] = round(v, 2) if v is not None else None
    out["dt_ms_max"] = round(max(dt), 1) if dt else None
    for g in GAPS_MS:
        out[f"gaps_gt_{g}ms"] = sum(1 for x in dt if x > g)
    return out


def seq_loss(seqs: dict[int, list[int]]) -> dict:
    """Потери датаграмм по номерам seq внутри каждой загрузки (boot_id)."""
    got = exp = 0
    for s in seqs.values():
        u = sorted(set(s))
        if not u:
            continue
        got += len(u)
        exp += u[-1] - u[0] + 1
    return {"datagrams": got, "expected": exp, "loss_pct": round(100.0 * (exp - got) / exp, 3) if exp else None}


class Collector:
    def __init__(self, keys: KeyStore):
        self.keys = keys
        self.frames: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
        self.ht: dict[str, int] = defaultdict(int)
        self.seqs: dict[str, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
        self.infer: dict[str, list[float]] = defaultdict(list)
        self.telem: dict[str, list] = defaultdict(list)
        self.bad = 0

    def feed(self, buf: bytes) -> None:
        try:
            dev = peek_device_id(buf)
            key = self.keys.get(dev)
            if key is None:
                self.bad += 1
                return
            p = parse_packet(buf, key)
        except ProtocolError:
            self.bad += 1
            return
        d = p.device_id
        self.seqs[d][p.boot_id].append(p.seq)
        if p.type in (TYPE_CSI, TYPE_CSI_SRC):
            for f in p.frames:
                src = f.src or "ap"
                self.frames[d][src].append(f.t_us)
                if src == "ap" and f.sig_mode == 1 and len(f.data) >= 256:
                    self.ht[d] += 1
        elif p.type == TYPE_PRED and p.prediction is not None:
            self.infer[d].append(float(p.prediction.infer_ms))
        elif p.type == TYPE_TELEM and p.telemetry is not None:
            self.telem[d].append(p.telemetry)

    def report(self, seconds: float) -> dict:
        devs = {}
        for d in sorted(set(self.frames) | set(self.telem) | set(self.infer)):
            tl = self.telem.get(d, [])
            heap = [t.heap_free for t in tl]
            links = {src: link_stats(ts, seconds) for src, ts in sorted(self.frames[d].items())}
            ap = links.get("ap", {}).get("frames", 0)
            devs[d] = {
                "fw_ver": tl[-1].fw_ver if tl else None,
                "model_ver": tl[-1].model_ver if tl else None,
                "boots_seen": sorted(self.seqs[d]),
                "uptime_s_end": tl[-1].uptime_s if tl else None,
                "heap_free_min": min(heap) if heap else None,
                "heap_free_p50": pct(heap, 50),
                "psram_free_min": min((t.psram_free for t in tl), default=None),
                "ht_share_ap": round(self.ht[d] / ap, 3) if ap else None,
                "infer_ms": {"n": len(self.infer[d]), "p50": pct(self.infer[d], 50), "p95": pct(self.infer[d], 95),
                             "p99": pct(self.infer[d], 99), "max": max(self.infer[d], default=None)},
                "uplink": seq_loss(self.seqs[d]),
                "links": links,
            }
        return {"devices": devs, "rejected": self.bad}


def git_sha() -> str:
    try:
        sha = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], text=True).strip()
        dirty = subprocess.call(["git", "-C", str(ROOT), "diff", "--quiet"]) != 0
        return sha + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def print_table(rep: dict) -> None:
    for d, r in rep["devices"].items():
        inf = r["infer_ms"]
        print(f"{d} fw={r['fw_ver']} model={r['model_ver']} boots={r['boots_seen']} heap_min={r['heap_free_min']} "
              f"uplink_loss={r['uplink']['loss_pct']}% infer_ms p50/p95/p99={inf['p50']}/{inf['p95']}/{inf['p99']} "
              f"(n={inf['n']})")
        for src, s in r["links"].items():
            print(f"   {src:>17} {s['hz']:7.1f} Hz  dt p50/p95/p99 {s['dt_ms_p50']}/{s['dt_ms_p95']}/{s['dt_ms_p99']} ms"
                  f"  max {s['dt_ms_max']}  gaps>50/200/1000: {s['gaps_gt_50ms']}/{s['gaps_gt_200ms']}/"
                  f"{s['gaps_gt_1000ms']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("seconds", type=float)
    ap.add_argument("--out", default="")
    ap.add_argument("--tag", default="")
    ap.add_argument("--sock", default=str(ROOT / "data" / "run" / "measure.sock"))
    a = ap.parse_args()
    keys = KeyStore(ROOT / "data" / "device_keys.json")
    path = Path(a.sock)
    if path.exists():
        path.unlink()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
    s.bind(str(path))
    os.chmod(path, 0o666)  # API в контейнере работает под uid 1000
    s.settimeout(0.5)
    col = Collector(keys)
    t0 = time.time()
    end = time.monotonic() + a.seconds
    try:
        while time.monotonic() < end:
            try:
                col.feed(s.recv(4096))
            except socket.timeout:
                continue
    finally:
        s.close()
        path.unlink(missing_ok=True)
    seconds = a.seconds
    rep = {"started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)), "seconds": seconds,
           "repo": git_sha(), "tag": a.tag, **col.report(seconds)}
    out = Path(a.out) if a.out else ROOT / "data" / "eval" / f"metrics-{time.strftime('%Y%m%d-%H%M%S', time.localtime(t0))}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    print_table(rep)
    print(f"-> {out}")
    if not rep["devices"]:
        print("no datagrams: is the API sending to measure.sock (wsn/__main__.py)?", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
