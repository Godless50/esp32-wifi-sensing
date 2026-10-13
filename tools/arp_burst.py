import socket, struct, sys, time
iface, my_ip, secs = sys.argv[1], sys.argv[2], float(sys.argv[3])
targets = [t.split("=") for t in sys.argv[4:]]  # ip=mac
my_mac = bytes.fromhex(open(f"/sys/class/net/{iface}/address").read().strip().replace(":", ""))
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0806))
s.bind((iface, 0))
def arp(ip, mac):
    dst = bytes.fromhex(mac.replace(":", ""))
    return dst + my_mac + b"\x08\x06" + struct.pack("!HHBBH", 1, 0x0800, 6, 4, 1) + my_mac + \
        socket.inet_aton(my_ip) + b"\x00" * 6 + socket.inet_aton(ip)
pk = [arp(ip, mac) for ip, mac in targets]
end, n = time.time() + secs, 0
while time.time() < end:
    for p in pk:
        s.send(p); n += 1
    time.sleep(0.05)
print("sent", n)
