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


def app_image(sha3=b"\xab\xcd\xef", n=5000):
    """Minimal ESP app image: header byte 0xE9, esp_app_desc_t magic at 0x20, ELF SHA256 at 0x20 + 144."""
    b = bytearray(n)
    b[0] = 0xE9
    b[0x20:0x24] = b"\x32\x54\xcd\xab"
    b[0x20 + 144:0x20 + 147] = sha3
    return bytes(b)


def test_firmware_id_matches_the_board_formula():
    from wsn.model_push import firmware_id
    assert firmware_id(app_image()) == 0x80ABCDEF
    assert firmware_id(blob()) is None
    assert firmware_id(b"\xe9" + bytes(400)) is None  # нет описания приложения


@pytest.mark.asyncio
async def test_firmware_push_finishes_when_telemetry_shows_the_new_firmware_id():
    p = ModelPusher({DEV: bytes(32)}, FakeIngest())
    p.transport = FakeTransport()
    st = p.start(DEV, app_image())
    assert st.kind == "firmware" and st.version == 0x80ABCDEF
    await asyncio.sleep(0.2)
    assert st.state == "sending"
    tel = Telemetry(1.0, 0.0, -50, 1, 1, 1, 0x80ABCDEF, 5, 2)  # fw_ver = firmware id
    p.on_packet(Packet(TYPE_TELEM, DEV, 8, 1, [], tel), 0.0)
    await asyncio.sleep(1.0)
    assert st.state == "done"


def test_model_version_with_the_firmware_bit_is_refused():
    p = ModelPusher({DEV: bytes(32)}, FakeIngest())

    async def go():
        with pytest.raises(ValueError):
            p.start(DEV, blob(version=0x80000001))

    asyncio.run(go())
