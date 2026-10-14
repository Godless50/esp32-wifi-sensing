"""Room model end to end on a synthetic flat: walk recording -> wsn.train -> data/model -> LiveInfer on the air."""

import json
import math

import numpy as np
import pytest

from wsn import train as T
from wsn.live import ltf_amplitude
from wsn.protocol import TYPE_CSI_SRC, CsiFrame, encode_packet
from wsn.store import Store
from wsn.zonemodel import EMPTY, LiveInfer, link_id

B1, B2, AP = "02:00:00:00:00:01", "02:00:00:00:00:02", "02:00:00:00:00:aa"
LINKS = [(B1, AP), (B1, B2), (B2, AP), (B2, B1)]
KEY = bytes(32)
HZ = 40.0
ROOM_A = [[0, 0], [3, 0], [3, 3], [0, 3]]
ROOM_B = [[3, 0], [6, 0], [6, 3], [3, 3]]
# (начало, конец, где) от начала записи; «out» — телефон за дверью, в квартире никого
SCRIPT = [(30, 110, "A"), (110, 190, "B"), (190, 260, "out"), (260, 320, "A"), (320, 380, "B"), (380, 450, "out")]


def ar(x, y):
    """Plan point (x, y) -> AR pose (board 1 at the AR origin, access point on +x)."""
    return [float(x), 1.2, -float(y)]


def disturbance(where: str, rx: str) -> float:
    if where == "out":
        return 0.0
    near = (where == "A" and rx == B1) or (where == "B" and rx == B2)
    return 0.25 if near else 0.04


def ltf(rng, t: float, link: int, amp_dist: float) -> bytes:
    k = np.arange(64)
    base = 22 + 6 * np.sin(k / 5.0 + link)
    wob = amp_dist * np.sin(2 * math.pi * 1.3 * t + k / 3.0 + rng.uniform(0, 6.28)) if amp_dist else 0.0
    amp = base * (1 + wob) + rng.normal(0, 0.25, 64)
    amp[[0, 1, 32]] = 0  # нулевые поднесущие
    iq = np.zeros((64, 2), np.int8)
    iq[:, 1] = np.clip(np.round(amp), 0, 127).astype(np.int8)
    raw = iq.tobytes()
    return raw + raw  # L-LTF + HT-LTF


def where_at(rel: float) -> str:
    for a, b, w in SCRIPT:
        if a <= rel < b:
            return w
    return "out"


def synth_session(store: Store, t0: float, seed: int = 0) -> str:
    rng = np.random.default_rng(seed)
    info = store.start_session("walk", B1, t0)
    ev = lambda t, kind, **kw: store.append_event({"seq": None, "t": t, "kind": kind, **kw})  # noqa: E731
    ev(t0 + 2, "device", mac=B1, role="board", p=ar(0, 0))
    ev(t0 + 4, "device", mac=AP, role="ap", p=ar(6, 0))
    ev(t0 + 6, "device", mac=B2, role="board", p=ar(5.5, 2.5))
    for t, name, poly in ((t0 + 10, "A", ROOM_A), (t0 + 20, "B", ROOM_B)):
        ev(t, "room_new", name=name)
        for i, (x, y) in enumerate(poly):
            ev(t + 1 + i, "corner", p=ar(x, y))
        ev(t + 6, "room_close")
    for a, b, w in SCRIPT:
        ev(t0 + a, "step", name=w, activity="empty" if w == "out" else "walk", room=w if w != "out" else None)
    # позы телефона 10 Гц: ходит по своей комнате, снаружи — далеко за планом
    seq = 0
    for i in range(int(450 * 10)):
        rel = i / 10
        w = where_at(rel)
        if w == "out":
            x, y = 12.0, 12.0
        else:
            cx = 1.5 if w == "A" else 4.5
            x, y = cx + 1.0 * math.cos(rel * 0.5), 1.5 + 1.0 * math.sin(rel * 0.5)
        seq += 1
        store.append_pose({"seq": seq, "t": t0 + rel, "t_phone": rel * 1000, "p": ar(x, y), "q": [0, 0, 0, 1]})
    # CSI всех плат
    seqs = {B1: 0, B2: 0}
    batch = {B1: [], B2: []}
    for i in range(int(450 * HZ)):
        t = t0 + i / HZ
        w = where_at(t - t0)
        for j, (rx, tx) in enumerate(LINKS):
            batch[rx].append(CsiFrame(int((t - t0 + 100) * 1e6), -50, -90, 1, 0, ltf(rng, t, j, disturbance(w, rx)), tx))
        for rx in (B1, B2):
            if len(batch[rx]) >= 4:
                seqs[rx] += 1
                store.append_csi_all(t + 0.005, encode_packet(TYPE_CSI_SRC, rx, 7, seqs[rx], KEY, frames=batch[rx]))
                batch[rx] = []
    store.stop_session(info.id, t0 + 450)
    return info.id


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    data = tmp_path_factory.mktemp("data")
    store = Store(data / "wsn.db", data / "sessions")
    sid = synth_session(store, 1000.0)
    rc = T.main(["--data", str(data), "--nets", "3", "--folds", "4", "--epochs", "40", "--jobs", "2"])
    assert rc == 0
    return data, sid


def test_training_learns_rooms_and_empty(trained):
    data, sid = trained
    meta = json.loads((data / "model" / "meta.json").read_text())
    assert meta["sessions"] == [sid]
    assert meta["zones"] == ["A", "B", EMPTY]
    assert sorted(meta["links"]) == sorted(link_id(r, t) for r, t in LINKS)
    cv = meta["metrics"]["heldout"]
    assert cv["zone_bal_acc"] >= 0.85, cv
    assert cv["nobody_home_bal_acc"] >= 0.85, cv
    status = json.loads((data / "run" / "train_status.json").read_text())
    assert status["state"] == "done" and status["version"] == meta["version"]
    assert "узнаётся" in (data / "model" / "report.txt").read_text()


def test_live_inference_matches_training(trained):
    data, _ = trained
    got = []
    inf = LiveInfer(data / "model", got.append)
    assert inf.reload(0.0)
    rng = np.random.default_rng(5)

    def run(where: str, t_from: float, seconds: int) -> list[dict]:
        out = []
        for s in range(seconds):
            for i in range(int(HZ)):
                t = t_from + s + i / HZ
                for j, (rx, tx) in enumerate(LINKS):
                    inf.on_frame(rx, tx, t, ltf_amplitude(ltf(rng, t, j, disturbance(where, rx)), "ht"))
            m = inf.tick(t_from + s + 1.0)
            if m:
                out.append(m)
        return out

    run("out", 5000.0, 30)
    inb = run("B", 5030.0, 30)[-10:]
    assert all(m["links_live"] == 4 and m["links_total"] == 4 for m in inb)
    assert np.mean([m["probs"]["B"] for m in inb]) > 0.7
    assert np.mean([m["empty_p"] for m in inb]) < 0.3
    ina = run("A", 5060.0, 30)[-10:]
    assert np.mean([m["probs"]["A"] for m in ina]) > 0.7
    out = run("out", 5090.0, 30)[-10:]
    assert np.mean([m["empty_p"] for m in out]) > 0.7
    assert got and got[-1]["type"] == "pred" and got[-1]["source"] == "model"


def test_recordings_on_other_links_are_not_trained(tmp_path):
    store = Store(tmp_path / "wsn.db", tmp_path / "sessions")
    synth_session(store, 1000.0)
    other = "aa:aa:aa:aa:aa:01|aa:aa:aa:aa:aa:02,aa:aa:aa:aa:aa:01|aa:aa:aa:aa:aa:03"
    rc = T.main(["--data", str(tmp_path), "--nets", "1", "--folds", "2", "--epochs", "2", "--jobs", "1",
                 "--live-links", other])
    assert rc == 2
    assert not (tmp_path / "model").exists()
    st = json.loads((tmp_path / "run" / "train_status.json").read_text())
    assert st["state"] == "error" and "нынешних платах" in st["message"]
