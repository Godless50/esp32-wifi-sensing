"""Широковещательные и групповые кадры по проводу сервера, по секундам: сколько и откуда (топ источников).

Точки доступа пересылают их в эфир на самой низкой скорости: пачка таких кадров занимает канал на секунды.
docker run --rm --network host --cap-add NET_RAW -v $PWD/tools:/t:ro python:3.12-slim python /t/sniff_bcast.py 130 enp11s0
"""
import collections
import socket
import struct
import sys
import time

dur, iface = float(sys.argv[1]), sys.argv[2]
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0003))
s.bind((iface, 0))
s.settimeout(0.2)
cnt, by = 0, collections.Counter()
nbytes = 0
sec = int(time.time())
end = time.time() + dur
while time.time() < end:
    try:
        pkt = s.recv(65535)
    except socket.timeout:
        pkt = None
    now = int(time.time())
    if now != sec:
        top = " ".join(f"{k}:{v}" for k, v in by.most_common(4))
        print(time.strftime("%H:%M:%S", time.localtime(sec)), cnt, nbytes, top, flush=True)
        cnt, nbytes, by = 0, 0, collections.Counter()
        sec = now
    if not pkt or not (pkt[0] & 1):  # групповой/широковещательный адрес получателя
        continue
    cnt += 1
    nbytes += len(pkt)
    et = struct.unpack_from(">H", pkt, 12)[0]
    src = pkt[6:12].hex(":")[-8:]
    kind = {0x0800: "ip", 0x0806: "arp", 0x86DD: "ip6"}.get(et, hex(et))
    if et == 0x0800 and len(pkt) > 34 and pkt[23] == 17:
        kind = "udp" + str(struct.unpack_from(">H", pkt, 36)[0])
    by[f"{src}/{kind}"] += 1
