"""ARP sweep of a /24: prints ip=mac of every host that answers (needs host network + NET_RAW)."""
import socket, struct, sys, time
iface, my_ip = sys.argv[1], sys.argv[2]
net = my_ip.rsplit(".", 1)[0]
my_mac = bytes.fromhex(open(f"/sys/class/net/{iface}/address").read().strip().replace(":", ""))
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0806))
s.bind((iface, 0))
s.settimeout(0.05)
found = {}
for rnd in range(3):
    for i in range(1, 255):
        ip = f"{net}.{i}"
        s.send(b"\xff" * 6 + my_mac + b"\x08\x06" + struct.pack("!HHBBH", 1, 0x0800, 6, 4, 1) + my_mac +
               socket.inet_aton(my_ip) + b"\x00" * 6 + socket.inet_aton(ip))
        time.sleep(0.004)
    end = time.time() + 1.5
    while time.time() < end:
        try:
            f = s.recv(128)
        except socket.timeout:
            continue
        if f[12:14] == b"\x08\x06" and struct.unpack("!H", f[20:22])[0] == 2:
            found[socket.inet_ntoa(f[28:32])] = ":".join(f"{b:02x}" for b in f[22:28])
for ip in sorted(found, key=lambda x: int(x.rsplit(".", 1)[1])):
    print(f"{ip}={found[ip]}")
