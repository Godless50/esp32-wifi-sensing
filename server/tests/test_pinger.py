import json
import struct

import pytest

from wsn.pinger import Pinger, PingerConfig, Target, checksum, echo_request


def test_echo_request_checksum_valid():
    pkt = echo_request(0, 7)
    assert pkt[0] == 8 and struct.unpack("!H", pkt[6:8])[0] == 7
    assert checksum(pkt) == 0  # корректная сумма даёт 0 при пересчёте


class FakeSock:
    def __init__(self, replies):
        self.sent, self.replies = [], list(replies)

    def setblocking(self, _):
        pass

    def sendto(self, data, addr):
        if addr[0] == "192.168.4.99":
            raise OSError("host unreachable")
        self.sent.append(addr[0])

    def recvfrom(self, _):
        if not self.replies:
            raise BlockingIOError
        return b"", (self.replies.pop(), 0)


def test_tick_sends_to_all_and_counts_replies():
    sock = FakeSock(["192.168.4.2", "192.168.4.14", "10.0.0.1"])
    p = Pinger(PingerConfig(20, [Target("192.168.4.2"), Target("192.168.4.14"), Target("192.168.4.99")]),
               sock_factory=lambda: sock)
    p.tick()
    assert sock.sent == ["192.168.4.2", "192.168.4.14"]  # недоступный адрес не ломает остальные
    st = {s["ip"]: s for s in p.stats()}
    assert st["192.168.4.2"]["replies"] == 1 and st["192.168.4.99"]["sent"] == 0


def test_config(tmp_path):
    f = tmp_path / "links.json"
    assert PingerConfig.load(f).targets == []
    f.write_text(json.dumps({"ping_hz": 25, "targets": [{"ip": "192.168.4.2", "mac": "d8:d6:68:b3:4c:23"}]}))
    c = PingerConfig.load(f)
    assert c.ping_hz == 25 and c.targets[0].mac == "d8:d6:68:b3:4c:23"
    f.write_text(json.dumps({"ping_hz": 500}))
    with pytest.raises(ValueError):
        PingerConfig.load(f)
