"""Первые несколько SSDP-пакетов (UDP 1900) от заданного IP: кто это и что он рассылает.

docker run --rm --network host --cap-add NET_RAW -v $PWD/tools:/t:ro python:3.12-slim python /t/sniff_ssdp_from.py 70 enp11s0 192.168.4.36
"""
import collections
import socket
import struct
import sys
import time

dur, iface, ip = float(sys.argv[1]), sys.argv[2], sys.argv[3]
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
s.bind((iface, 0))
s.settimeout(0.5)
end = time.time() + dur
shown = 0
kinds = collections.Counter()
while time.time() < end and shown < 400:
    try:
        pkt = s.recv(65535)
    except socket.timeout:
        continue
    iph = pkt[14:]
    if iph[9] != 17 or socket.inet_ntoa(iph[12:16]) != ip:
        continue
    ihl = (iph[0] & 15) * 4
    sport, dport = struct.unpack_from(">HH", iph, ihl)
    if dport != 1900 and sport != 1900:
        continue
    body = iph[ihl + 8:].decode(errors="replace")
    lines = body.split("\r\n")
    hdr = {l.split(":", 1)[0].upper(): l.split(":", 1)[1].strip() for l in lines[1:] if ":" in l}
    kinds[(lines[0], hdr.get("NT", hdr.get("ST", "")))] += 1
    if shown < 2:
        print(socket.inet_ntoa(iph[16:20]), body[:600].replace("\r\n", " | "))
    shown += 1
print("total", shown)
for k, v in kinds.most_common(12):
    print(v, k)
