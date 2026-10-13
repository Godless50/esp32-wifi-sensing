"""tools/link_metrics.py: перцентили, интервалы по часам платы, потери датаграмм, разбор живых пакетов."""

import importlib.util
from pathlib import Path

import pytest

from wsn.protocol import CsiFrame, Telemetry, encode_packet, TYPE_CSI, TYPE_TELEM

_spec = importlib.util.spec_from_file_location(
    "link_metrics", Path(__file__).resolve().parents[2] / "tools" / "link_metrics.py")
lm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lm)


def test_pct_nearest_rank():
    xs = list(range(1, 101))
    assert lm.pct(xs, 50) == 50
    assert lm.pct(xs, 95) == 95
    assert lm.pct(xs, 99) == 99
    assert lm.pct([], 50) is None
    assert lm.pct([7.0], 99) == 7.0


def test_link_stats_intervals_and_gaps():
    t = [i * 10_000 for i in range(100)]  # 100 кадров через 10 мс
    t += [t[-1] + 300_000]                # один провал 300 мс
    s = lm.link_stats(t, seconds=1.0)
    assert s["frames"] == 101
    assert s["dt_ms_p50"] == pytest.approx(10.0)
    assert s["dt_ms_max"] == pytest.approx(300.0)
    assert s["gaps_gt_50ms"] == 1 and s["gaps_gt_200ms"] == 1 and s["gaps_gt_1000ms"] == 0


def test_seq_loss_per_boot():
    r = lm.seq_loss({1: [0, 1, 2, 4], 2: [10, 11]})  # в загрузке 1 потерян seq 3
    assert r == {"datagrams": 6, "expected": 7, "loss_pct": round(100 / 7, 3)}


class _Keys:
    def __init__(self, keys):
        self.keys = keys

    def get(self, d):
        return self.keys.get(d)


def test_collector_parses_signed_packets():
    key = bytes(range(32))
    dev = "7c:4f:ad:bc:9b:54"
    col = lm.Collector(_Keys({dev: key}))
    frames = [CsiFrame(t_us=1000 * i, rssi=-40, noise=-90, sig_mode=1, cwb=0, data=bytes(256)) for i in range(3)]
    col.feed(encode_packet(TYPE_CSI, dev, 5, 0, key, frames=frames))
    tel = Telemetry(csi_hz=100.0, drop_ratio=0.0, rssi_avg=-40, heap_free=120000, psram_free=6000000,
                    uptime_s=10, fw_ver=1, model_ver=2, wifi_state=2)
    col.feed(encode_packet(TYPE_TELEM, dev, 5, 1, key, telemetry=tel))
    col.feed(b"garbage")
    rep = col.report(1.0)
    d = rep["devices"][dev]
    assert d["links"]["ap"]["frames"] == 3
    assert d["ht_share_ap"] == 1.0
    assert d["heap_free_min"] == 120000 and d["fw_ver"] == 1
    assert d["uplink"]["loss_pct"] == 0.0
    assert rep["rejected"] == 1
