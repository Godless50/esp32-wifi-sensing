"""Synthetic CSI sender for testing the pipeline without a board.

python -m wsn.replay --host 127.0.0.1 --port 5005 --key-hex <64hex> --mac 02:00:00:00:00:01 --hz 100 --seconds 30
"""

from __future__ import annotations

import argparse
import math
import random
import socket
import time

import numpy as np

from .protocol import MAX_DATAGRAM, HEADER_LEN, HMAC_LEN, TYPE_CSI, TYPE_TELEM, CsiFrame, Telemetry, encode_packet

_FRAME_HDR = 14
_MAX_FRAMES = 10


def _ltf(t: float) -> bytes:
    k = np.arange(64)
    amp = 20 + 10 * np.sin(2 * math.pi * 0.5 * t + k / 8.0)
    iq = np.zeros((64, 2), dtype=np.int8)
    iq[:, 1] = np.clip(amp, -127, 127).astype(np.int8)  # real; imag = 0
    return iq.tobytes()


def synth_frames(n: int, hz: float, t0_us: int) -> list[CsiFrame]:
    step = int(round(1e6 / hz))
    out = []
    for i in range(n):
        t_us = t0_us + i * step
        ltf = _ltf(t_us / 1e6)
        out.append(CsiFrame(t_us, -45, -92, 1, 0, ltf + ltf))  # L-LTF + HT-LTF = 256 байт
    return out


def packets_for(frames: list[CsiFrame], mac: str, key: bytes, boot_id: int, seq0: int) -> list[bytes]:
    pkts, batch, size, seq = [], [], HEADER_LEN + HMAC_LEN, seq0
    for f in frames:
        need = _FRAME_HDR + len(f.data)
        if batch and (size + need > MAX_DATAGRAM or len(batch) >= _MAX_FRAMES):
            pkts.append(encode_packet(TYPE_CSI, mac, boot_id, seq, key, frames=batch))
            seq += 1
            batch, size = [], HEADER_LEN + HMAC_LEN
        batch.append(f)
        size += need
    if batch:
        pkts.append(encode_packet(TYPE_CSI, mac, boot_id, seq, key, frames=batch))
    return pkts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5005)
    ap.add_argument("--key-hex", required=True)
    ap.add_argument("--mac", required=True)
    ap.add_argument("--hz", type=float, default=100.0)
    ap.add_argument("--seconds", type=float, default=30.0)
    a = ap.parse_args()
    key = bytes.fromhex(a.key_hex)
    boot_id = random.getrandbits(32)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    seq, t_start = 0, time.monotonic()
    tick = 0.05
    per_tick = max(1, int(round(a.hz * tick)))
    sent_frames, last_telem = 0, 0.0
    while (now := time.monotonic() - t_start) < a.seconds:
        frames = synth_frames(per_tick, a.hz, int(now * 1e6))
        for p in packets_for(frames, a.mac, key, boot_id, seq):
            sock.sendto(p, (a.host, a.port))
            seq += 1
        sent_frames += per_tick
        if now - last_telem >= 1.0:
            t = Telemetry(per_tick / tick, 0.0, -45, 200_000, 8_000_000, int(now), 1, 0, 2)
            sock.sendto(encode_packet(TYPE_TELEM, a.mac, boot_id, seq, key, telemetry=t), (a.host, a.port))
            seq += 1
            last_telem = now
        time.sleep(max(0.0, tick - ((time.monotonic() - t_start) - now)))
    print(f"sent {sent_frames} frames in {a.seconds:.0f}s")


if __name__ == "__main__":
    main()
