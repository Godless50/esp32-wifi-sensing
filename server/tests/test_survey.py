"""Survey of the home network: commands to the board and its report."""

import hashlib
import hmac
import struct

import pytest

from wsn.protocol import (CMD_LINKS, CMD_SURVEY, SURVEY_F_CHOSEN, SURVEY_F_RANDOM_MAC, TYPE_CMD, TYPE_SURVEY,
                          Survey, SurveyEntry, encode_packet, parse_packet)
from wsn.survey import COPIES, SurveyControl

KEY = bytes(range(32))
DEV = "02:00:00:00:c9:04"


class FakeTransport:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))


class FakeIngest:
    def __init__(self, boot=105):
        self.addrs = {DEV: ("192.168.4.31", 5005)}
        self.boot = boot

    def boot_of(self, dev):
        return self.boot if dev == DEV else None


class Pusher:
    def __init__(self):
        self.transport = FakeTransport()


def decode_cmd(buf: bytes):
    body, tag = buf[:-16], buf[-16:]
    assert hmac.new(KEY, body, hashlib.sha256).digest()[:16] == tag
    magic, ver, ptype, dev, boot, seq, n = struct.unpack_from("<4sBB6sIIB", body)
    assert (magic, ver, ptype, n) == (b"WSN1", 1, TYPE_CMD, 1)
    cmd, ln = body[21], body[22]
    assert len(body) == 23 + ln
    return dev.hex(":"), boot, seq, cmd, body[23:]


def make():
    pusher = Pusher()
    ctl = SurveyControl({DEV: KEY}, FakeIngest(), pusher)
    return ctl, pusher.transport


def test_start_sends_signed_command_bound_to_boot():
    ctl, tr = make()
    rep = ctl.start(DEV, 60, 6, ["192.168.4.23"])
    assert rep["state"] == "requested"
    assert len(tr.sent) == COPIES and len({d for d, _ in tr.sent}) == 1  # одна команда, один seq, три копии
    dev, boot, seq, cmd, args = decode_cmd(tr.sent[0][0])
    assert (dev, boot, cmd) == (DEV, 105, CMD_SURVEY) and tr.sent[0][1] == ("192.168.4.31", 5005)
    assert args == bytes([60, 6, 1, 192, 168, 4, 23])
    ctl.set_links(DEV, [("d8:d6:68:b3:4c:23", "192.168.4.2")])
    _, _, seq2, cmd2, args2 = decode_cmd(tr.sent[-1][0])
    assert seq2 > seq and cmd2 == CMD_LINKS and args2 == bytes([1]) + bytes.fromhex("d8d668b34c23") + bytes([192, 168, 4, 2])


def test_unknown_board_and_bad_args():
    ctl, _ = make()
    with pytest.raises(RuntimeError):
        ctl.start("02:00:00:00:00:09")
    with pytest.raises(ValueError):
        ctl.start(DEV, 5)


def test_report_kept():
    ctl, _ = make()
    sv = Survey(3, 100, 9, (SurveyEntry("192.168.4.2", "d8:d6:68:b3:4c:23", 0.95, -56, 100, SURVEY_F_CHOSEN),
                            SurveyEntry("192.168.4.60", "da:a1:19:00:00:01", 0.8, -60, 100, SURVEY_F_RANDOM_MAC)))
    p = parse_packet(encode_packet(TYPE_SURVEY, DEV, 105, 7, KEY, survey=sv), KEY)
    ctl.on_packet(p, 0.0)
    r = ctl.status()["surveys"][DEV]
    assert r["state"] == "done" and r["responders"] == 9 and r["boot_id"] == 105
    assert r["entries"][0]["chosen"] and r["entries"][1]["random_mac"] and not r["entries"][1]["chosen"]
