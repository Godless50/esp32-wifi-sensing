from types import SimpleNamespace

import pytest

from wsn.sounder import Sounder


class Tr:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))


def test_sounds_live_boards_and_skips_busy_or_silent_ones():
    ingest = SimpleNamespace(addrs={"a": ("192.0.2.1", 1), "b": ("192.0.2.2", 2), "c": ("192.0.2.3", 3)})
    pusher = SimpleNamespace(pushes={"b": SimpleNamespace(state="sending")}, transport=None)
    seen = {"a": 100.0, "b": 100.0, "c": 50.0}
    s = Sounder(ingest, pusher, hz=100, last_seen=seen.get)
    s.transport = Tr()
    assert s.tick(now=101.0) == 1  # b — идёт загрузка, c — молчит дольше 10 с
    data, addr = s.transport.sent[0]
    assert addr == ("192.0.2.1", 1) and data[:4] == b"WSNS" and len(data) == 8
    pusher.pushes["b"].state = "done"
    assert s.tick(now=101.0) == 2 and s.transport.sent[-1][0] != data  # счётчик растёт


def test_no_transport_no_send_and_rate_bounds():
    s = Sounder(SimpleNamespace(addrs={"a": ("x", 1)}), None, hz=50)
    assert s.tick() == 0
    with pytest.raises(ValueError):
        Sounder(SimpleNamespace(addrs={}), None, hz=0)
