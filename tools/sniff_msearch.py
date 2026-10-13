"""Кто и когда шлёт SSDP M-SEARCH (поиск устройств), и когда начинаются пачки NOTIFY от заданного IP.

docker run --rm --network host --cap-add NET_RAW -v $PWD/tools:/t:ro python:3.12-slim python /t/sniff_msearch.py 130 enp11s0 192.168.4.36
"""
import socket
import struct
import sys
import time

dur, iface, storm_ip = float(sys.argv[1]), sys.argv[2], sys.argv[3]
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0003))
s.bind((iface, 0))
s.settimeout(0.5)
end = time.time() + dur
last_notify = 0.0
while time.time() < end:
    try:
        pkt, addr = s.recvfrom(65535)
    except socket.timeout:
        continue
    if pkt[12:14] != b"\x08\x00":
        continue
    iph = pkt[14:]
    if iph[9] != 17:
        continue
    ihl = (iph[0] & 15) * 4
    sport, dport = struct.unpack_from(">HH", iph, ihl)
    if dport != 1900:
        continue
    src = socket.inet_ntoa(iph[12:16])
    body = iph[ihl + 8:ihl + 8 + 200].decode(errors="replace")
    now = time.time()
    ts = time.strftime("%H:%M:%S", time.localtime(now)) + f".{int(now * 1000) % 1000:03d}"
    if body.startswith("M-SEARCH"):
        st = [l for l in body.split("\r\n") if l.upper().startswith("ST:")]
        print(ts, "M-SEARCH from", src, pkt[6:12].hex(":"), "out" if addr[2] == 4 else "in", st[:1], flush=True)
    elif src == storm_ip and now - last_notify > 5:
        print(ts, "NOTIFY burst starts from", src, flush=True)
    if src == storm_ip:
        last_notify = now
