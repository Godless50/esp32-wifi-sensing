"""Длины CSI по передатчикам в пакетах плат (UDP 5005) за N секунд — без участия сервера.

docker run --rm --network host --cap-add NET_RAW -v $PWD/tools:/t:ro python:3.12-slim python /t/sniff_csi_len.py 10 [MAC платы]
"""
import collections
import socket
import struct
import sys
import time

dur = float(sys.argv[1]) if len(sys.argv) > 1 else 10
only = sys.argv[2].lower() if len(sys.argv) > 2 else ""
F = struct.Struct("<QbbBBH")
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
s.settimeout(0.5)
hist = collections.Counter()
end = time.time() + dur
while time.time() < end:
    try:
        pkt = s.recv(65535)
    except socket.timeout:
        continue
    ip = pkt[14:]
    if len(ip) < 28 or ip[9] != 17:
        continue
    ihl = (ip[0] & 15) * 4
    dport = struct.unpack_from(">H", ip, ihl + 2)[0]
    if dport != 5005:
        continue
    b = ip[ihl + 8:]
    if b[:4] != b"WSN1" or b[5] != 4:
        continue
    dev = ":".join(f"{x:02x}" for x in b[6:12])
    if only and dev != only:
        continue
    off, body = 21, b[:-16]
    for _ in range(b[20]):
        t_us, rssi, noise, sm, cwb, dl = F.unpack_from(body, off)
        src = ":".join(f"{x:02x}" for x in body[off + 14:off + 20])
        off += 20 + dl
        hist[(dev[-5:], src[-5:], sm, dl)] += 1
for k, v in sorted(hist.items()):
    print(k, v)
