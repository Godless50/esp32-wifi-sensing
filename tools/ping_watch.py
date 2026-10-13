"""Пинг нескольких адресов 5 раз в секунду; по секундам — сколько ответов. Видно, общий ли провал у всей сети.

docker run --rm --network host --cap-add NET_RAW -v $PWD/tools:/t:ro python:3.12-slim python /t/ping_watch.py 150 192.168.4.2 ...
"""
import os
import socket
import struct
import sys
import time

dur = float(sys.argv[1])
hosts = sys.argv[2:]
s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
s.setblocking(False)
ident = os.getpid() & 0xFFFF


def csum(b: bytes) -> int:
    if len(b) % 2:
        b += b"\0"
    t = sum(struct.unpack(f"!{len(b) // 2}H", b))
    t = (t >> 16) + (t & 0xFFFF)
    t += t >> 16
    return ~t & 0xFFFF


got = {h: 0 for h in hosts}
seq = 0
sec = int(time.time())
end = time.time() + dur
nxt = time.time()
while time.time() < end:
    now = time.time()
    if now >= nxt:
        nxt += 0.2
        seq = (seq + 1) & 0xFFFF
        for h in hosts:
            hdr = struct.pack("!BBHHH", 8, 0, 0, ident, seq)
            pkt = struct.pack("!BBHHH", 8, 0, csum(hdr + b"wsnwatch"), ident, seq) + b"wsnwatch"
            try:
                s.sendto(pkt, (h, 0))
            except OSError:
                pass
    try:
        while True:
            data, addr = s.recvfrom(2048)
            ihl = (data[0] & 15) * 4
            if data[ihl] == 0 and struct.unpack_from("!H", data, ihl + 4)[0] == ident and addr[0] in got:
                got[addr[0]] += 1
    except BlockingIOError:
        pass
    if int(now) != sec:
        print(time.strftime("%H:%M:%S", time.localtime(sec)), " ".join(f"{h.split('.')[-1]}={got[h]}" for h in hosts),
              flush=True)
        got = {h: 0 for h in hosts}
        sec = int(now)
    time.sleep(0.01)
