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


def test_settings_blob_and_push_done_when_the_board_activates():
    from wsn.model_push import CFG_MASK, links_blob
    from wsn.protocol import TYPE_MODEL_ACK, ModelAck
    b = links_blob(["02:00:00:00:a1:3c", "02:00:00:00:c9:04"])
    assert b[:5] == b"WSNC\x02" and len(b) == 17
    with pytest.raises(ValueError):
        links_blob(["00:00:00:00:00:01"] * 15)

    async def go():
        p = ModelPusher({DEV: bytes(32)}, FakeIngest())
        p.transport = FakeTransport()
        st = p.start(DEV, b)
        assert st.kind == "settings" and st.version & CFG_MASK == CFG_MASK
        await asyncio.sleep(0.2)
        p.on_packet(Packet(TYPE_MODEL_ACK, DEV, 7, 2, [], model_ack=ModelAck(st.version, len(b), 2, 0, 0)), 0.0)
        await asyncio.sleep(0.8)
        assert st.state == "done"

    asyncio.run(go())


def test_settings_blob_with_promiscuous_flag_and_beacon_rate():
    from wsn.model_push import links_blob
    b = links_blob(["02:00:00:00:c9:04"], promisc=False, beacon_hz=50)
    assert b == b"WSNC\x01" + bytes.fromhex("02000000c904") + b"\x00\x32"

    async def go():
        p = ModelPusher({DEV: bytes(32)}, FakeIngest())
        p.transport = FakeTransport()
        assert p.start(DEV, b).kind == "settings"

    asyncio.run(go())


def test_settings_blob_with_bssid():
    from wsn.model_push import links_blob
    b = links_blob(["02:00:00:00:c9:04"], promisc=False, beacon_hz=50, bssid="02:00:00:00:a1:3c")
    assert len(b) == 13 + 6 and b[-6:] == bytes.fromhex("02000000a13c")


class OldBoard:
    """Board firmware before the re-ack fix: takes chunks only in order, ignores repeats silently and acknowledges
    only when its state changes. Every ack after the first chunk up to a whole window ahead is lost."""

    def __init__(self, pusher, total):
        self.p, self.total, self.received, self.lost = pusher, total, 0, False

    def sendto(self, data, addr):
        from wsn.protocol import HEADER_LEN, TYPE_MODEL_ACK, ModelAck
        ver, total, off, n = struct.unpack_from("<IIIH", data, HEADER_LEN)
        if off != self.received:
            return
        self.received += n
        if 1024 < self.received <= 9 * 1024:
            self.lost = True  # подтверждения потерялись в эфире: сервер знает только 1024
            return
        ack = ModelAck(ver, self.received, 1, 1, 0)
        asyncio.get_running_loop().call_soon(self.p.on_packet, Packet(TYPE_MODEL_ACK, DEV, 7, 1, model_ack=ack), 0.0)


@pytest.mark.asyncio
async def test_push_recovers_when_the_ack_of_a_whole_window_is_lost():
    p = ModelPusher({DEV: bytes(32)}, FakeIngest())
    b = blob(n=20000)
    board = OldBoard(p, len(b))
    p.transport = board
    st = p.start(DEV, b)
    for _ in range(60):
        await asyncio.sleep(0.1)
        if board.received == len(b):
            break
    assert board.lost and board.received == len(b)
    p.on_packet(Packet(TYPE_TELEM, DEV, 7, 2, [], Telemetry(1.0, 0.0, -50, 1, 1, 1, 2, 5, 2)), 0.0)
    await asyncio.sleep(1.2)
    assert st.state == "done"


@pytest.mark.asyncio
async def test_a_silent_board_never_gets_more_than_two_windows_per_round():
    """A board that stops answering must not be flooded: the resend window stays bounded."""
    p = ModelPusher({DEV: bytes(32)}, FakeIngest())
    p.transport = FakeTransport()
    st = p.start(DEV, blob(n=100_000))
    await asyncio.sleep(4.0)  # ~6 кругов без единого ответа
    assert st.state == "sending" and st.sent <= 2 * 8 * 1024
