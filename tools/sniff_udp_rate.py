"""Пакетов в секунду по проводу сервера: от плат на UDP 5005 и от сервера к платам (зонды), по каждой плате.

docker run --rm --network host --cap-add NET_RAW -v $PWD/tools:/t:ro python:3.12-slim python /t/sniff_udp_rate.py 120 enp11s0
"""
import collections
import socket
import struct
import sys
import time

dur = float(sys.argv[1])
iface = sys.argv[2]
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0003))
s.bind((iface, 0))
s.settimeout(0.2)
cur = collections.Counter()
sec = int(time.time())
end = time.time() + dur
while time.time() < end:
    try:
        pkt, addr = s.recvfrom(65535)
    except socket.timeout:
        pkt = None
    now = int(time.time())
    if now != sec:
        print(time.strftime("%H:%M:%S", time.localtime(sec)),
              " ".join(f"{k}={v}" for k, v in sorted(cur.items())), flush=True)
        cur.clear()
        sec = now
    if not pkt or pkt[12:14] != b"\x08\x00":
        continue
    ip = pkt[14:]
    if ip[9] != 17:
        continue
    ihl = (ip[0] & 15) * 4
    sport, dport = struct.unpack_from(">HH", ip, ihl)
    src, dst = socket.inet_ntoa(ip[12:16]), socket.inet_ntoa(ip[16:20])
    if dport == 5005:
        cur["in." + src.split(".")[-1]] += 1
    elif sport == 5005:
        cur["out." + dst.split(".")[-1]] += 1
