"""Live recognition + online learning from what is on the air right now (host process, server GPU).

Input: copy of every board datagram from the API (wsn.mirror -> data/run/csi.sock).
Every second, per board: the last 4 s of every link -> build_seq.link_seq -> the live model ensemble
(data/ml/live_model, CPU inference) -> POST /api/predict -> 3D scene. The window is kept in a ring buffer.

Online learning, every --adapt-min minutes when the buffer has enough fresh windows (GPU, background thread):
  * pseudo-labels: activity where the ensemble is confident (>= --conf), zone only where somebody moves and the
    zone is confident; the rest of the live windows still train the "somebody moves" head where it is confident;
  * anchor: all labelled windows (scripted + spoken sessions) are mixed in, so the model cannot drift away from
    what is known; augmentation is the same as in offline training;
  * each net continues from its current weights with a small learning rate for a few epochs;
  * guard: the candidate must not be worse than the current model on the labelled scripted windows (zone and
    activity balanced accuracy, tolerance --tol) and must not collapse to one class on the live buffer.
    Accepted -> the old model is kept as live_model.prev, the new one goes live; rejected -> nothing changes.
  Every attempt is appended to data/ml/live_model/history.jsonl.

Extras (--extras, ml/train_multi.py ensemble): number of moving people (total and per zone), posture, arms / legs
bent, joint angles — added to every /api/predict body. Learns online too, but its teacher is physics, not itself:
every second the tracker's link-only position (GET /api/person -> "geo": which radio links are disturbed, from the
plan geometry, no neural net) is stored with the window; windows where that estimate is sure and at most one person
moves become "1 moving person in zone Z" labels, links disturbed in rooms that one person cannot explain at once
become "at least N people in those rooms", long still periods become "nobody moves". Mixed with the labelled
anchor (scripted sessions, superposed and physics data); the update is kept only if the people count on the
labelled real windows did not get worse.
Only boards listed in --devices are predicted: the models were trained on the links of board 1.

usage: tools/learner.sh   (runs this with the ROCm venv, logs to data/ml/learner.log)
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import shutil
import socket
import threading
import time
import urllib.request
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import torch

from build_rich import ltf_complex
from build_seq import NC, NT, link_seq
from nn_common import bal, squash
from train_final import labels
from train_nn_torch import DEV, Net, fit
from wsn.keys import KeyStore
from wsn.protocol import TYPE_CSI_SRC, ProtocolError, parse_packet, peek_device_id

log = logging.getLogger("learner")
WIN_S, KEEP_S, LAG_S = 4.0, 12.0, 0.3


def load_extras(path: Path):
    from train_multi import MultiNet
    meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
    nets = []
    for i in range(meta["seeds"]):
        n = MultiNet(len(meta["links"]), NC)
        n.load_state_dict(torch.load(path / f"net{i}.pt", map_location="cpu"))
        nets.append(n.eval())
    return meta, nets


@torch.no_grad()
def extras_body(meta, nets, X) -> dict:
    from train_multi import ANG_MU, ANG_SD
    x = torch.tensor(squash(X[None]), dtype=torch.float32)
    acc = {}
    for n in nets:
        o = n(x)
        for k in ("n_move", "cmove", "pose", "arms", "legs"):
            acc[k] = acc.get(k, 0) + o[k].softmax(-1)[0] / len(nets)
        acc["ang"] = acc.get("ang", 0) + (o["ang"][0] * ANG_SD + ANG_MU) / len(nets)
    r = lambda v: [round(float(x), 3) for x in v]
    return {"people_probs": r(acc["n_move"]),
            "counts_probs": {z: r(acc["cmove"][i]) for i, z in enumerate(meta["zones6"])},
            "pose_probs": {z: round(float(p), 3) for z, p in zip(meta["poses"], acc["pose"])},
            "arms_probs": {z: round(float(p), 3) for z, p in zip(meta["arms"], acc["arms"])},
            "legs_probs": {z: round(float(p), 3) for z, p in zip(meta["legs"], acc["legs"])},
            "angles": [round(float(v), 1) for v in acc["ang"]]}


def load_model(path: Path):
    meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
    nets = []
    for i in range(meta["seeds"]):
        n = Net(len(meta["links"]), NC, arch=meta.get("arch", "dil"))
        n.load_state_dict(torch.load(path / f"net{i}.pt", map_location="cpu"))
        nets.append(n.eval())
    return meta, nets


@torch.no_grad()
def ensemble(nets, X):
    """Mean probabilities of the ensemble: activity [N,4], zone [N,4], moving [N]."""
    x = torch.tensor(squash(X), dtype=torch.float32)
    pa = pz = pm = 0
    for n in nets:
        n = n.to(x.device).eval()
        a, z, m = n(x)
        pa, pz, pm = pa + a.softmax(1), pz + z.softmax(1), pm + torch.sigmoid(m)
    k = len(nets)
    return (pa / k).numpy(), (pz / k).numpy(), (pm / k).numpy()


class Stream:
    """Per (board, transmitter) the last KEEP_S seconds of complex LTF frames, on this process's monotonic clock."""

    def __init__(self, keys: KeyStore):
        self.keys = keys
        self.ht = defaultdict(deque)      # (dev, src) -> deque[(t, H)]
        self.legacy = defaultdict(deque)  # кадры 11a/g (только L-LTF), напр. робот-пылесос
        self.packets = 0

    def feed(self, buf: bytes, now: float) -> None:
        try:
            p = parse_packet(buf, self.keys.get(peek_device_id(buf)))
        except (ProtocolError, TypeError, ValueError):
            return
        if p.type != TYPE_CSI_SRC or not p.frames:
            return
        self.packets += 1
        last = p.frames[-1].t_us
        for f in p.frames:
            lag = (last - f.t_us) / 1e6
            t = now - lag if 0 <= lag < 2 else now
            if len(f.data) >= 256:
                self.ht[(p.device_id, f.src)].append((t, ltf_complex(f.data, 128)))
            elif len(f.data) >= 128:
                self.legacy[(p.device_id, f.src)].append((t, ltf_complex(f.data, 0)))
        for q in (*self.ht.values(), *self.legacy.values()):
            while q and q[0][0] < now - KEEP_S:
                q.popleft()

    def devices(self) -> set[str]:
        return {d for d, _ in self.ht} | {d for d, _ in self.legacy}

    def window(self, dev: str, links: list[str], t0: float) -> np.ndarray:
        """[L, NT, NC] in the training format; HT-LTF unless the link sends (almost) only legacy frames."""
        out = np.zeros((len(links), NT, NC), np.float32)
        for i, m in enumerate(links):
            h, g = self.ht.get((dev, m)), self.legacy.get((dev, m))
            nh, ng = len(h or ()), len(g or ())
            q = h if nh and nh >= 0.2 * (nh + ng) else g
            if not q:
                continue
            t = np.fromiter((x for x, _ in q), float, len(q))
            H = np.stack([y for _, y in q])
            o = np.argsort(t, kind="stable")
            out[i] = link_seq(t[o], H[o], t0)
        return out


def post(url: str, body: dict) -> None:
    req = urllib.request.Request(url, json.dumps(body).encode(), {"content-type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=2).read()
    except OSError as e:
        log.debug("post failed: %s", e)


class Learner:
    def __init__(self, a):
        self.a = a
        self.model_dir = Path(a.model)
        self.meta, self.nets = load_model(self.model_dir)
        self.extras = None
        self.extras_anchor = None
        if a.extras and Path(a.extras, "meta.json").exists():
            self.extras = load_extras(Path(a.extras))
            log.info("extras model v%s (%d nets): people / pose", self.extras[0]["version"], len(self.extras[1]))
        self.lock = threading.Lock()
        self.buf_X: deque = deque(maxlen=a.buffer)  # живые окна для дообучения
        self.buf_t: deque = deque(maxlen=a.buffer)
        self.buf_teach: deque = deque(maxlen=a.buffer)  # состояние трекера (физика) на момент окна
        self.since_adapt = 0
        self.busy = False
        d = np.load(a.anchor)
        self.anchor_X = d["X"].astype(np.float32)
        self.anchor_y = labels(d)
        sess = d["session"]
        self.val = np.isin(sess, ["20261013-053501-ee64", "20261013-065019-45cd"]) & (self.anchor_y[0] >= 0)
        log.info("model v%s (%d nets), anchor %d windows", self.meta["version"], len(self.nets), len(self.anchor_X))

    def teacher(self) -> dict | None:
        """Tracker state right now: link-only zone (physics), moving / still, lower bound of people."""
        try:
            with urllib.request.urlopen(self.a.api + "/api/person", timeout=0.5) as r:
                p = json.loads(r.read()).get("person") or {}
        except (OSError, ValueError):
            return None
        g = p.get("geo") or {}
        return {"geo_zone": g.get("zone"), "geo_p": g.get("p", 0.0), "moving": bool(p.get("moving")),
                "min_people": int(p.get("min_people", 0)), "min_rooms": p.get("min_rooms") or [],
                "links_active": int(p.get("links_active", 0)),
                "status": p.get("status"), "still_s": p.get("still_s", 0)}

    def predict(self, dev: str, X: np.ndarray) -> None:
        with self.lock:
            nets, meta = self.nets, self.meta
        pa, pz, pm = ensemble(nets, X[None])
        ai, zi = int(pa[0].argmax()), int(pz[0].argmax())
        body = {"device_id": dev, "zone": meta["zones"][zi], "zone_p": round(float(pz[0, zi]), 3),
                "activity": meta["activities"][ai], "activity_p": round(float(pa[0, ai]), 3),
                "moving_p": round(float(pm[0]), 3),
                "probs": {z: round(float(p), 3) for z, p in zip(meta["zones"], pz[0])},
                "model": f"v{meta['version']}"}
        if self.extras is not None:
            try:
                body.update(extras_body(*self.extras, X))
            except Exception:
                log.exception("extras failed")
        post(self.a.api + "/api/predict", body)
        if np.abs(X).sum() > 0:
            self.buf_X.append(X.astype(np.float16))
            self.buf_t.append(time.time())
            self.buf_teach.append(self.teacher() if self.extras is not None else None)
            self.since_adapt += 1

    def scores(self, nets):
        X, (ya, yz, _) = self.anchor_X[self.val], (v[self.val] for v in self.anchor_y)
        pa, pz, _ = ensemble(nets, X)
        z = yz >= 0
        return {"activity": bal(ya, pa.argmax(1)), "zone": bal(yz[z], pz.argmax(1)[z])}

    def adapt(self) -> None:
        """One online update; runs in a background thread so live predictions keep flowing."""
        t0 = time.time()
        a = self.a
        try:
            with self.lock:
                nets, meta = self.nets, self.meta
            L = np.stack(list(self.buf_X)).astype(np.float32)
            pa, pz, pm = ensemble(nets, L)
            ca, cz = pa.max(1), pz.max(1)
            pl_a = np.where(ca >= a.conf, pa.argmax(1), -1)
            pl_z = np.where((pl_a > 0) & (cz >= a.conf), pz.argmax(1), -1)
            pl_m = np.where(pm >= a.conf, 1, np.where(pm <= 1 - a.conf, 0, -1))
            keep = (pl_a >= 0) | (pl_m >= 0)
            X = np.concatenate([self.anchor_X, L[keep]])
            ya = np.concatenate([self.anchor_y[0], pl_a[keep]])
            yz = np.concatenate([self.anchor_y[1], pl_z[keep]])
            ym = np.concatenate([self.anchor_y[2], pl_m[keep]])
            cand = []
            for i, n in enumerate(nets):
                st = {k: v.clone() for k, v in n.state_dict().items()}
                new = fit(X, ya, yz, ym, a.adapt_epochs, 100 + i, init_state=st, lr=a.lr, arch=meta.get("arch", "dil"))
                cand.append(new.cpu().eval())
            old_s, new_s = self.scores(nets), self.scores(cand)
            na = ensemble(cand, L)[0].argmax(1)
            share = float(np.bincount(na, minlength=4).max() / len(na))
            old_share = float(np.bincount(pa.argmax(1), minlength=4).max() / len(na))
            ok = (new_s["zone"] >= old_s["zone"] - a.tol and new_s["activity"] >= old_s["activity"] - a.tol
                  and (share < 0.97 or share <= old_share + 0.02))
            rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "from": meta["version"], "live_windows": int(len(L)),
                   "pseudo_activity": int((pl_a >= 0).sum()), "pseudo_zone": int((pl_z >= 0).sum()),
                   "old": old_s, "new": new_s, "top_class_share": round(share, 3), "accepted": bool(ok),
                   "seconds": round(time.time() - t0)}
            if ok:
                ver = int(time.time())
                prev = self.model_dir.with_name(self.model_dir.name + ".prev")
                if prev.exists():
                    shutil.rmtree(prev)
                shutil.copytree(self.model_dir, prev)
                for i, n in enumerate(cand):
                    torch.save(n.state_dict(), self.model_dir / f"net{i}.pt")
                meta = {**meta, "version": ver, "online_from": rec["from"], "online_live_windows": rec["live_windows"]}
                (self.model_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
                np.savez_compressed(self.model_dir.parent / f"live_buffer_{ver}.npz", X=L.astype(np.float16),
                                    t=np.array(self.buf_t), pa=pa.astype(np.float16), pz=pz.astype(np.float16))
                with self.lock:
                    self.nets, self.meta = cand, meta
                rec["to"] = ver
            with open(self.model_dir / "history.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            log.info("online update: %s", rec)
        except Exception:
            log.exception("online update failed")
        try:
            if self.extras is not None:
                self.adapt_extras()
        except Exception:
            log.exception("extras online update failed")
        finally:
            self.since_adapt = 0
            self.busy = False

    def adapt_extras(self) -> None:
        """Online update of the people / pose model with physics (link geometry) as the teacher."""
        import train_multi as tm
        t0 = time.time()
        a = self.a
        meta, nets = self.extras
        zones6 = meta["zones6"]
        X, T = list(self.buf_X), list(self.buf_teach)
        rows, n_move, cm = [], [], []
        for x, te in zip(X, T):
            if not te:
                continue
            if te["moving"] and te["min_people"] >= 2 and te["min_rooms"]:  # линии: движение в разных комнатах сразу
                c = np.zeros(6, int)
                for r in te["min_rooms"]:
                    if r in zones6:
                        c[zones6.index(r)] = 1
                rows.append(x); n_move.append(int(min(te["min_people"], 5))); cm.append(c)
            elif (te["moving"] and te["min_people"] <= 1 and te.get("links_active", 0) >= 1  # учитель — только по
                  and te["geo_p"] >= a.geo_conf and te["geo_zone"] in zones6):         # возмущённым линиям
                c = np.zeros(6, int); c[zones6.index(te["geo_zone"])] = 1
                rows.append(x); n_move.append(1); cm.append(c)
            elif te["status"] == "неподвижен" and te["still_s"] >= 30:
                rows.append(x); n_move.append(0); cm.append(np.zeros(6, int))
        if len(rows) < 100:
            log.info("extras online update skipped: %d teacher windows", len(rows))
            return
        if self.extras_anchor is None:
            rng = np.random.default_rng(0)
            keep = tm.load_all(Path(a.anchor).parent, rng, names={"seq", "real_mix", "physics"})
            masks = [np.ones(len(p["group"]), bool) for p in keep]
            self.extras_anchor = tm._cat(keep, masks, rng)  # X, y, src (физика ограничена CAP)
        AX, Ay, As = self.extras_anchor
        L = np.stack(rows).astype(np.float32)
        ly = tm._blank(len(L))
        ly["n_move"], ly["cmove"], ly["move"] = np.array(n_move), np.stack(cm), np.array(n_move)
        Xa = np.concatenate([AX, L])
        ya = {k: np.concatenate([Ay[k], ly[k]]) for k in Ay}
        sa = np.concatenate([As, np.full(len(L), "live")])
        val = (As == "seq") & (Ay["n_move"] >= 0)

        def score(ns):
            P = tm.predict(ns, AX[val])
            return bal(Ay["n_move"][val], P["n_move"].argmax(1))

        old = score([copy.deepcopy(n).to(tm.DEV) for n in nets])  # копии: живые сети остаются на CPU
        cand = [tm.fit(Xa, ya, sa, a.adapt_epochs, 200 + i, init_state={k: v.clone() for k, v in n.state_dict().items()},
                       lr=a.lr) for i, n in enumerate(nets)]
        new = score(cand)
        ok = new >= old - a.tol
        rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "model": "extras", "from": meta["version"],
               "teacher_windows": len(L), "moving_labels": int(sum(n_move)), "old_n_move": old, "new_n_move": new,
               "accepted": bool(ok), "seconds": round(time.time() - t0)}
        if ok:
            d = Path(a.extras)
            prev = d.with_name(d.name + ".prev")
            if prev.exists():
                shutil.rmtree(prev)
            shutil.copytree(d, prev)
            ver = int(time.time())
            for i, n in enumerate(cand):
                torch.save(n.cpu().state_dict(), d / f"net{i}.pt")
            meta = {**meta, "version": ver, "online_from": rec["from"], "online_teacher_windows": len(L)}
            (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
            with self.lock:
                self.extras = (meta, [n.cpu().eval() for n in cand])
            rec["to"] = ver
        with open(Path(a.extras) / "history.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log.info("extras online update: %s", rec)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="../data")
    ap.add_argument("--model", default="../data/ml/live_model")
    ap.add_argument("--anchor", default="../data/ml/seq_v1.npz")
    ap.add_argument("--api", default="http://127.0.0.1:8080")
    ap.add_argument("--adapt-min", type=float, default=30.0)
    ap.add_argument("--min-new", type=int, default=600, help="new live windows needed before an update")
    ap.add_argument("--buffer", type=int, default=7200, help="live windows kept (1 per second)")
    ap.add_argument("--adapt-epochs", type=int, default=8)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--conf", type=float, default=0.85)
    ap.add_argument("--tol", type=float, default=0.01)
    ap.add_argument("--extras", default="../data/ml/live_model_v4", help="train_multi.py ensemble (people, pose)")
    ap.add_argument("--devices", default="7c:4f:ad:bc:9b:54", help="boards the models were trained for")
    ap.add_argument("--geo-conf", type=float, default=0.7, help="link-only zone certainty for a teacher label")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    torch.set_num_threads(4)
    data = Path(a.data)
    sock_path = data / "run" / "csi.sock"
    sock_path.parent.mkdir(exist_ok=True)
    if sock_path.exists():
        sock_path.unlink()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(str(sock_path))
    os.chmod(sock_path, 0o660)
    sock.settimeout(0.2)
    stream = Stream(KeyStore(data / "device_keys.json"))
    lr = Learner(a)
    next_pred, last_adapt, last_log = time.monotonic() + 1, time.monotonic(), 0.0
    while True:
        try:
            buf = sock.recv(4096)
            stream.feed(buf, time.monotonic())
        except socket.timeout:
            pass
        now = time.monotonic()
        if now >= next_pred:
            next_pred = now + 1.0
            for dev in stream.devices() & set(a.devices.lower().split(",")):
                lr.predict(dev, stream.window(dev, lr.meta["links"], now - LAG_S - WIN_S))
        if (not lr.busy and lr.since_adapt >= a.min_new and now - last_adapt >= a.adapt_min * 60):
            lr.busy, last_adapt = True, now
            threading.Thread(target=lr.adapt, daemon=True).start()
        if now - last_log > 300:
            last_log = now
            log.info("packets %d, live windows %d, new since update %d, model v%s",
                     stream.packets, len(lr.buf_X), lr.since_adapt, lr.meta["version"])


if __name__ == "__main__":
    main()
