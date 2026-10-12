from wsn.ingest import IngestService
from wsn.keys import KeyStore
from wsn.live import LiveHub, ltf_amplitude
from wsn.protocol import MAX_DATAGRAM
from wsn.replay import packets_for, synth_frames

KEY = bytes(range(32))
MAC = "02:00:00:00:00:01"


def test_synth_frames_shape():
    frames = synth_frames(100, 100.0, 0)
    assert len(frames) == 100
    assert [f.t_us for f in frames[:3]] == [0, 10_000, 20_000]
    assert all(len(f.data) == 256 and f.sig_mode == 1 for f in frames)


def test_synth_frames_vary_over_time():
    frames = synth_frames(50, 100.0, 0)
    a0 = ltf_amplitude(frames[0].data, "ht")
    a1 = ltf_amplitude(frames[25].data, "ht")
    assert (abs(a0 - a1)).max() > 1.0


def test_packets_accepted_by_ingest(tmp_path):
    ks = KeyStore(tmp_path / "k.json")
    ks.set(MAC, KEY)
    hub = LiveHub()
    svc = IngestService(ks, hub.on_packet)
    pkts = packets_for(synth_frames(100, 100.0, 0), MAC, KEY, boot_id=7, seq0=0)
    assert all(len(p) <= MAX_DATAGRAM for p in pkts)
    for i, p in enumerate(pkts):
        assert svc.handle(p, i * 0.05)
    assert svc.counters["accepted"] == 20
    assert hub.devices()[0].counters["frames"] == 100
