"""Какие IPv4-пакеты с необычным протоколом (не ICMP/IGMP/TCP/UDP) ходят по локальной сети.

Платы падают, когда lwIP выбрасывает такой пакет (ip4_input, ветка default -> pbuf_free).
Запуск (нужен сырой сокет):
  docker run --rm --network host --cap-add NET_RAW -v $PWD/tools:/t:ro python:3.12-slim python /t/sniff_ipproto.py 60
"""
import collections
import socket
import struct
import sys
import time

dur = float(sys.argv[1]) if len(sys.argv) > 1 else 60
IFACE = sys.argv[2] if len(sys.argv) > 2 else ""
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
s.settimeout(1.0)
seen = collections.Counter()
examples = {}
total = 0
end = time.time() + dur
while time.time() < end:
    try:
        pkt, addr = s.recvfrom(65535)
    except socket.timeout:
        continue
    if IFACE and addr[0] != IFACE:
        continue
    total += 1
    ip = pkt[14:]
    if len(ip) < 20:
        continue
    proto = ip[9]
    if proto in (1, 2, 6, 17):
        continue
    src, dst = socket.inet_ntoa(ip[12:16]), socket.inet_ntoa(ip[16:20])
    key = (proto, src, dst, addr[0])
    seen[key] += 1
    examples.setdefault(key, (len(ip), ip[:40].hex(), pkt[6:12].hex(":")))
print(f"{total} IPv4 frames in {dur:.0f} s")
for (proto, src, dst, ifc), n in seen.most_common(20):
    ln, hx, smac = examples[(proto, src, dst, ifc)]
    print(f"proto {proto:3d} {src:>15} -> {dst:<15} via {ifc} from {smac}: {n} pkts, len {ln}, head {hx[:48]}")
