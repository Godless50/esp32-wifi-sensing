import asyncio
import struct

import pytest

from wsn.model_push import ModelPusher
from wsn.protocol import TYPE_TELEM, Packet, Telemetry

DEV = "02:00:00:00:00:01"


class FakeIngest:
    def __init__(self):
        self.addrs = {DEV: ("192.0.2.5", 40000)}

    def boot_of(self, dev):
        return 7


class FakeTransport:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))


def blob(version=5, n=3000):
    return b"WSNM" + struct.pack("<I", version) + bytes(120) + bytes(range(256)) * (n // 256)


def test_start_outside_the_event_loop_leaves_no_stuck_push():
    p = ModelPusher({DEV: bytes(32)}, FakeIngest())
    with pytest.raises(RuntimeError):
        p.start(DEV, blob())
    assert p.status() == []


@pytest.mark.asyncio
async def test_push_sends_chunks_and_finishes_when_the_board_reports_the_version():
    p = ModelPusher({DEV: bytes(32)}, FakeIngest())
    p.transport = FakeTransport()
    st = p.start(DEV, blob())
    with pytest.raises(RuntimeError):
        p.start(DEV, blob())  # одна отправка на плату за раз
    await asyncio.sleep(0.2)
    assert st.state == "sending" and p.transport.sent and st.sent > 0
    tel = Telemetry(1.0, 0.0, -50, 1, 1, 1, 2, 5, 2)
    p.on_packet(Packet(TYPE_TELEM, DEV, 7, 1, [], tel), 0.0)
    await asyncio.sleep(1.0)
    assert st.state == "done"
