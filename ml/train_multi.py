"""Multi-task net: activity / zone / moving (as before) + number of people + body pose + joint angles.

Data (all in the per-link sequence format, 10 links of board 1; single-link external data sits in one random slot):
  seq_v1.npz            our sessions: act / zone / moving; scripted windows also give n_move (0 still, 1 moving)
  multi_v1.npz          build_multi.py: real_mix (superposed real recordings, 1-3 moving + 1 still) and physics
                        (0-3 articulated bodies): n_move, n_all, per-zone counts; single-person physics: pose, arms,
                        legs, angles
  ext_atanasuke_v2.npz  ESP32-C6, video skeleton: pose (stand / sit / lie / transition) and 2D joint angles
  ext_small.npz         WiCount (ESP32-S3, 0-3 people) n_all; ESP32-S3 HAR postures

--eval: two runs, each holding out one scripted session (its seq windows and its real_mix) plus external pieces:
  run A: WiCount file tails, every 8th atanasuke project, the whole HAR set (cross-environment pose)
  run B: the whole WiCount set (cross-environment count), atanasuke projects 1::8, odd HAR files (in-domain)
--final: ensemble on all data -> data/ml/live_model_v4 (net{i}.pt, meta.json).

usage: tools/gpu_run.sh ml/train_multi.py --eval      (HSA_OVERRIDE_GFX_VERSION=10.3.0, .venv-rocm)
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

from nn_common import ACT_NAMES, SCRIPTED, act_class, bal

DEV = torch.device("cuda" if torch.cuda.is_available() else "cpu")
ZONES4 = ["зал", "кухня", "коридор", "санузел"]
ZONES6 = ["спальня", "зал", "кухня", "коридор", "санузел", "комната"]
POSE = ["стоит", "сидит", "лежит", "идёт", "переход"]
ARMS = ["опущены", "согнуты", "подняты"]
LEGS = ["прямые", "согнуты"]
POSE9 = {0: 0, 3: 1, 6: 2, 1: 4, 2: 4, 4: 4, 5: 4, 7: 4, 8: 4}  # atanasuke -> POSE
ANG_MU, ANG_SD = 150.0, 30.0
N_MAX = 5  # в квартире бывает до 5 человек; по зонам — 0, 1, 2, 3+
HEADS_W = {"act": 1.0, "zone": 1.0, "move": 0.5, "n_move": 1.0, "n_all": 0.5, "cmove": 0.5, "pose": 1.0,
           "arms": 0.3, "legs": 0.3, "ang": 0.5}
CAP = {"seq": None, "real_mix": None, "physics": 10000, "atanasuke": 16000, "wicount": None, "har": None}


class MultiNet(nn.Module):
    """enc / zone / trunk / act / move have the same names and shapes as train_nn_torch.Net (arch s2), so the
    board export of the old heads keeps working; the new heads sit on a second trunk."""

    def __init__(self, L: int, C: int, w: int = 32):
        super().__init__()
        self.enc = nn.Sequential(nn.Conv1d(C, 24, 5, padding=2), nn.ReLU(),
                                 nn.Conv1d(24, w, 5, stride=2, padding=2), nn.ReLU(),
                                 nn.Conv1d(w, w, 5, padding=2), nn.ReLU(), nn.AdaptiveAvgPool1d(1))
        self.zone = nn.Sequential(nn.Linear(L * w, 64), nn.ReLU(), nn.Dropout(0.3), nn.Linear(64, 4))
        self.trunk = nn.Sequential(nn.Linear(2 * w, 48), nn.ReLU(), nn.Dropout(0.3))
        self.act, self.move = nn.Linear(48, 4), nn.Linear(48, 1)
        self.trunk2 = nn.Sequential(nn.Linear(2 * w, 64), nn.ReLU(), nn.Dropout(0.2))
        self.n_move, self.n_all = nn.Linear(64, N_MAX + 1), nn.Linear(64, N_MAX + 1)
        self.pose, self.arms, self.legs, self.ang = nn.Linear(64, 5), nn.Linear(64, 3), nn.Linear(64, 2), nn.Linear(64, 4)
        self.cmove = nn.Sequential(nn.Linear(L * w, 64), nn.ReLU(), nn.Dropout(0.3), nn.Linear(64, 6 * 4))

    def forward(self, x):
        B, L, T, C = x.shape
        e = self.enc(x.reshape(B * L, T, C).transpose(1, 2)).reshape(B, L, -1)
        pooled = torch.cat([e.max(1).values, e.mean(1)], 1)
        h, h2, flat = self.trunk(pooled), self.trunk2(pooled), e.reshape(B, -1)
        return {"act": self.act(h), "zone": self.zone(flat), "move": self.move(h)[:, 0], "n_move": self.n_move(h2),
                "n_all": self.n_all(h2), "pose": self.pose(h2), "arms": self.arms(h2), "legs": self.legs(h2),
                "ang": self.ang(h2), "cmove": self.cmove(flat).reshape(B, 6, 4)}


# ---------------------------------------------------------------- данные
def _blank(n):
    return {"act": np.full(n, -1), "zone": np.full(n, -1), "move": np.full(n, -1), "n_move": np.full(n, -1),
            "n_all": np.full(n, -1), "cmove": np.full((n, 6), -1), "pose": np.full(n, -1), "arms": np.full(n, -1),
            "legs": np.full(n, -1), "ang": np.full((n, 4), np.nan, np.float32)}


def _pad(X1, L, rng):
    """Single-link windows [n,1,T,C] -> [n,L,T,C] with the link in a random slot."""
    out = np.zeros((len(X1), L) + X1.shape[2:], np.float32)
    out[np.arange(len(X1)), rng.integers(L, size=len(X1))] = X1[:, 0]
    return out


def load_all(ml: Path, rng, names=None) -> list[dict]:
    """names: load only these parts (seq, real_mix, physics, atanasuke, wicount, har)."""
    want = lambda n: names is None or n in names
    parts = []
    d = np.load(ml / "seq_v1.npz")
    act, zone, sess = d["act"], d["zone"], d["session"]
    y = _blank(len(act))
    y["act"] = act_class(act)
    y["zone"] = np.array([{1: 0, 2: 1, 3: 2, 4: 3}.get(int(z), -1) if a >= 1 else -1 for z, a in zip(zone, act)])
    scripted = np.isin(sess, SCRIPTED)
    y["move"] = np.where(scripted, np.where(y["act"] < 0, -1, (y["act"] > 0).astype(int)), d["moving"])
    y["n_move"] = np.where(scripted & (act >= 0), (act >= 2).astype(int), -1)
    # n_all неизвестно: во время сценария остальные домашние спали в спальне и комнате
    cm = np.repeat(np.where(scripted & (act >= 0) & (zone >= 0), 0, -1)[:, None], 6, 1)
    for i in np.flatnonzero(scripted & (act >= 2) & (zone >= 0)):
        cm[i, zone[i]] = 1
    y["cmove"] = cm
    parts.append({"name": "seq", "X": d["X"].astype(np.float32), "y": y, "session": sess, "group": sess})
    d = np.load(ml / ("multi_v2.npz" if (ml / "multi_v2.npz").exists() else "multi_v1.npz"))
    for src in [s for s in ("real_mix", "physics") if want(s)]:
        m = d["src"] == src
        y = _blank(int(m.sum()))
        y["n_move"] = np.clip(d["n_move"][m], 0, N_MAX)
        y["n_all"] = np.where(d["n_all"][m] < 0, -1, np.clip(d["n_all"][m], 0, N_MAX))
        y["cmove"] = np.clip(d["cmove"][m], 0, 3)
        y["move"] = (d["n_move"][m] > 0).astype(int)
        y["pose"], y["arms"], y["legs"] = d["pose"][m], d["arms"][m], d["legs"][m]
        y["ang"] = d["angles"][m]
        g = d["session"][m] if src == "real_mix" else np.array([f"ph{i % 10}" for i in range(int(m.sum()))])
        parts.append({"name": src, "X": d["X"][m].astype(np.float32), "y": y, "session": d["session"][m], "group": g})
    if not want("atanasuke") and not want("wicount") and not want("har"):
        return parts
    d = np.load(ml / "ext_atanasuke_v2.npz")
    lab, ang = d["label9"], d["angles"]
    keep = np.flatnonzero((lab >= 0) | np.isfinite(ang).all(1))
    y = _blank(len(keep))
    y["pose"] = np.array([POSE9.get(int(v), -1) for v in lab[keep]])
    y["ang"] = ang[keep]
    y["n_all"] = np.ones(len(keep), int)
    parts.append({"name": "atanasuke", "X": _pad(d["X"][keep], 10, rng), "y": y,
                  "session": np.full(len(keep), ""), "group": d["project"][keep]})
    d = np.load(ml / "ext_small.npz")
    for src, name in (("wicount", "wicount"), ("esp32s3_har", "har")):
        m = d["src"] == src
        y = _blank(int(m.sum()))
        if name == "wicount":
            y["n_all"] = d["n_all"][m]
        else:
            y["pose"] = d["pose"][m]
            y["n_all"] = np.ones(int(m.sum()), int)
        grp = np.char.add(np.char.add(d["group"][m], "|"), d["part"][m])
        parts.append({"name": name, "X": _pad(d["X"][m], 10, rng), "y": y, "session": np.full(int(m.sum()), ""),
                      "group": grp})
    return parts


def split(parts, test_session, wicount, atan_mod, har):
    """Boolean test mask per part for one evaluation run."""
    masks = []
    atan_projects = sorted(set(next(p for p in parts if p["name"] == "atanasuke")["group"].tolist()))
    held = set(atan_projects[atan_mod::8])
    for p in parts:
        n, g = len(p["group"]), p["group"]
        if p["name"] in ("seq", "real_mix"):
            m = p["session"] == test_session
        elif p["name"] == "atanasuke":
            m = np.isin(g, list(held))
        elif p["name"] == "wicount":
            m = np.ones(n, bool) if wicount == "all" else np.char.endswith(g.astype(str), "|tail")
        elif p["name"] == "har":
            num = np.array([int("".join(c for c in s.split("|")[0] if c.isdigit()) or 0) for s in g])
            m = np.ones(n, bool) if har == "all" else (num % 2 == 1)
        elif p["name"] == "physics":
            m = np.char.startswith(g.astype(str), "ph0")
        else:
            m = np.zeros(n, bool)
        masks.append(m)
    return masks


def _cat(parts, masks, rng, cap=True):
    Xs, ys, src = [], {k: [] for k in _blank(0)}, []
    for p, m in zip(parts, masks):
        idx = np.flatnonzero(m)
        c = CAP.get(p["name"]) if cap else None
        if c is not None and len(idx) > c:
            idx = rng.choice(idx, c, replace=False)
        Xs.append(p["X"][idx])
        for k in ys:
            ys[k].append(p["y"][k][idx])
        src += [p["name"]] * len(idx)
    return np.concatenate(Xs), {k: np.concatenate(v) for k, v in ys.items()}, np.array(src)


# ---------------------------------------------------------------- обучение
def _augment(x, key, gen):
    B, L, T, C = x.shape
    x = x * torch.exp(torch.empty(B, L, 1, 1, device=DEV).uniform_(np.log(0.5), np.log(2.0), generator=gen))
    s = torch.empty(B, 1, device=DEV).uniform_(0.7, 1.4, generator=gen)
    sh = torch.empty(B, 1, device=DEV).uniform_(-5, 5, generator=gen)
    src = ((torch.arange(T, device=DEV)[None] - T / 2) / s + T / 2 + sh).clamp(0, T - 1)
    lo = src.floor().long()
    hi = (lo + 1).clamp(max=T - 1)
    w = (src - lo)[:, None, :, None]
    ex = lambda i: i[:, None, :, None].expand(B, L, T, C)
    x = torch.gather(x, 2, ex(lo)) * (1 - w) + torch.gather(x, 2, ex(hi)) * w
    x = x + torch.randn(x.shape, device=DEV, generator=gen) * 0.05 * (x.abs().mean((2, 3), keepdim=True) + 1e-3)
    live = x.abs().sum((2, 3)) > 0
    drop = (torch.rand(B, L, device=DEV, generator=gen) < 0.12) & (live.sum(1, keepdim=True) > 1)
    x = x * (~drop)[:, :, None, None]
    j = torch.randperm(B, device=DEV, generator=gen)
    mix = (key == key[j]) & (j != torch.arange(B, device=DEV)) & (torch.rand(B, device=DEV, generator=gen) < 0.3)
    lam = torch.empty(B, 1, 1, 1, device=DEV).uniform_(0.6, 0.9, generator=gen)
    x = torch.where(mix[:, None, None, None], lam * x + (1 - lam) * x[j], x)
    return torch.sign(x) * torch.log1p(x.abs())


def _keys(y, src):
    names = {s: i for i, s in enumerate(sorted(set(src.tolist())))}
    k = np.array([names[s] for s in src], np.int64)
    for f in ("act", "zone", "n_move", "n_all", "pose", "arms", "legs"):
        k = k * 7 + (y[f] + 1)
    for z in range(6):
        k = k * 5 + (y["cmove"][:, z] + 1)
    has_ang = np.isfinite(y["ang"]).any(1)
    k[has_ang] = -1 - np.flatnonzero(has_ang)  # окна с углами не смешиваем
    return k


def fit(X, y, src, epochs, seed, enc_state=None, bs=128, init_state=None, lr=2e-3):
    """init_state: continue from a trained MultiNet (online updates, small lr)."""
    rng = np.random.default_rng(seed)
    torch.manual_seed(seed)
    gen = torch.Generator(device=DEV).manual_seed(seed)
    net = MultiNet(X.shape[1], X.shape[3]).to(DEV)
    if init_state is not None:
        net.load_state_dict(init_state)
    elif enc_state is not None:
        net.enc.load_state_dict(enc_state)
    rest = [p for n, p in net.named_parameters() if not n.startswith("enc.")]
    enc_lr = lr * (0.3 if enc_state is not None and init_state is None else 1.0)
    opt = torch.optim.AdamW([{"params": net.enc.parameters(), "lr": enc_lr}, {"params": rest, "lr": lr}],
                            weight_decay=1e-3)
    steps = epochs * ((len(X) + bs - 1) // bs)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, [g["lr"] * 1.5 for g in opt.param_groups], total_steps=steps)
    ya = y["act"]
    cnt = np.array([(ya == c).sum() for c in range(4)], float)
    cw = torch.tensor(np.where(cnt > 0, cnt.sum() / (4 * np.maximum(cnt, 1)), 0), dtype=torch.float32, device=DEV)
    T = {k: torch.tensor(np.nan_to_num(v, nan=-999) if k == "ang" else v.astype(np.int64),
                         dtype=torch.float32 if k == "ang" else torch.int64, device=DEV) for k, v in y.items()}
    Xd = torch.tensor(X, device=DEV)
    key = torch.tensor(_keys(y, src), device=DEV)
    ce = nn.CrossEntropyLoss(reduction="none")

    def cls(logits, t, w=None):
        m = t >= 0
        if not m.any():
            return logits.sum() * 0
        l = ce(logits[m], t[m])
        if w is not None:
            l = l * w[t[m]]
        return l.mean()

    t_start = time.time()
    for ep in range(epochs):
        if ep and ep % 5 == 0:
            print(f"  seed {seed}: epoch {ep}/{epochs}, {time.time() - t_start:.0f} s", flush=True)
        net.train()
        for idx in torch.randperm(len(Xd), device=DEV).split(bs):
            out = net(_augment(Xd[idx], key[idx], gen))
            t = {k: v[idx] for k, v in T.items()}
            mv = t["move"] >= 0
            ang_m = t["ang"] > -900
            loss = (HEADS_W["act"] * cls(out["act"], t["act"], cw) + HEADS_W["zone"] * cls(out["zone"], t["zone"])
                    + HEADS_W["n_move"] * cls(out["n_move"], t["n_move"]) + HEADS_W["n_all"] * cls(out["n_all"], t["n_all"])
                    + HEADS_W["pose"] * cls(out["pose"], t["pose"]) + HEADS_W["arms"] * cls(out["arms"], t["arms"])
                    + HEADS_W["legs"] * cls(out["legs"], t["legs"])
                    + HEADS_W["cmove"] * cls(out["cmove"].reshape(-1, 4), t["cmove"].reshape(-1)))
            if mv.any():
                loss = loss + HEADS_W["move"] * nn.functional.binary_cross_entropy_with_logits(out["move"][mv], t["move"][mv].float())
            if ang_m.any():
                loss = loss + HEADS_W["ang"] * nn.functional.smooth_l1_loss(out["ang"][ang_m], (t["ang"][ang_m] - ANG_MU) / ANG_SD)
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
    return net


@torch.no_grad()
def predict(nets, X, bs=2048):
    acc = {}
    for i in range(0, len(X), bs):
        x = torch.tensor(X[i:i + bs], device=DEV)
        x = torch.sign(x) * torch.log1p(x.abs())
        s = {}
        for net in nets:
            net.eval()
            o = net(x)
            for k, v in o.items():
                p = torch.sigmoid(v) if k == "move" else v * ANG_SD + ANG_MU if k == "ang" else v.softmax(-1)
                s[k] = s.get(k, 0) + p / len(nets)
        for k, v in s.items():
            acc.setdefault(k, []).append(v.cpu().numpy())
    return {k: np.concatenate(v) for k, v in acc.items()}


# ---------------------------------------------------------------- отчёт
def report(P, y, src):
    r = {}
    def b(name, m, k, yy=None):
        t = (y[k] if yy is None else yy)[m]
        ok = t >= 0
        if ok.sum() < 20:
            return
        p = P[k][m][ok].argmax(-1)
        r[name] = {"bal_acc": bal(t[ok], p), "acc": round(float((p == t[ok]).mean()), 3), "n": int(ok.sum()),
                   "confusion": {int(c): np.bincount(p[t[ok] == c], minlength=P[k].shape[-1]).tolist() for c in np.unique(t[ok])}}
    s = lambda *names: np.isin(src, names)
    b("zone (сессия)", s("seq"), "zone")
    b("activity (сессия)", s("seq"), "act")
    m = s("seq") & (y["move"] >= 0)
    if m.sum() >= 20:
        r["moving (сессия)"] = {"bal_acc": bal(y["move"][m], (P["move"][m] > 0.5).astype(int))}
    b("n_move: реальные окна сессии (0/1)", s("seq"), "n_move")
    b("n_move: наложение записей сессии (1-3)", s("real_mix"), "n_move")
    b("n_move: физика (отложенная)", s("physics"), "n_move")
    for nm in ("real_mix", "physics"):
        m = s(nm)
        if m.any():
            pz = P["cmove"][m].argmax(-1)
            tz = y["cmove"][m]
            r[f"по зонам, все 6 зон верно ({nm})"] = round(float((pz == tz).all(1).mean()), 3)
            r[f"по зонам, точность на зону ({nm})"] = round(float((pz == tz).mean()), 3)
    b("n_all: WiCount (другое помещение)", s("wicount"), "n_all")
    b("поза: atanasuke (отложенные проекты)", s("atanasuke"), "pose")
    b("поза: ESP32-S3 HAR", s("har"), "pose")
    b("поза: физика", s("physics"), "pose")
    b("руки: физика", s("physics"), "arms")
    b("ноги: физика", s("physics"), "legs")
    for nm in ("atanasuke", "physics"):
        m = s(nm) & np.isfinite(y["ang"]).all(1)
        if m.sum() >= 20:
            err = np.abs(P["ang"][m] - y["ang"][m]).mean(0)
            base = np.abs(y["ang"][m] - np.median(y["ang"][m], 0)).mean(0)
            r[f"углы локоть Л/П, колено Л/П: ошибка, град ({nm})"] = np.round(err, 1).tolist()
            r[f"углы: ошибка «всегда медиана», град ({nm})"] = np.round(base, 1).tolist()
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ml", default="data/ml")
    ap.add_argument("--encoder", default="data/ml/pretrain_v3/encoder.pt")
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--final", action="store_true")
    ap.add_argument("--out", default="data/ml/live_model_v4")
    a = ap.parse_args()
    ml = Path(a.ml)
    rng = np.random.default_rng(0)
    t0 = time.time()
    parts = load_all(ml, rng)
    print({p["name"]: len(p["group"]) for p in parts}, "device", DEV, flush=True)
    enc = torch.load(a.encoder, map_location="cpu") if a.encoder else None
    res = {}
    if a.eval:
        runs = [("A", SCRIPTED[1], "tail", 0, "all"), ("B", SCRIPTED[0], "all", 1, "odd")]
        for name, sess, wic, mod, har in runs:
            te = split(parts, sess, wic, mod, har)
            tr = [~m for m in te]
            Xtr, ytr, str_ = _cat(parts, tr, rng)
            Xte, yte, ste = _cat(parts, te, rng, cap=False)
            nets = [fit(Xtr, ytr, str_, a.epochs, s, enc) for s in range(a.seeds)]
            r = report(predict(nets, Xte), yte, ste)
            res[f"run {name}: тест сессия {sess}, WiCount {wic}, HAR {har}"] = r
            print(name, json.dumps(r, ensure_ascii=False), flush=True)
        res["seconds"] = round(time.time() - t0)
        out = ml / "multi_eval_v1.json"
        out.write_text(json.dumps(res, ensure_ascii=False, indent=1))
        print("saved", out)
    if a.final:
        Xtr, ytr, str_ = _cat(parts, [np.ones(len(p["group"]), bool) for p in parts], rng)
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        for s in range(a.seeds):
            net = fit(Xtr, ytr, str_, a.epochs, 100 + s, enc)
            torch.save(net.state_dict(), out / f"net{s}.pt")
        from build_rich import LINKS
        meta = {"version": int(time.time()), "arch": "multi_s2", "links": list(LINKS), "zones": ZONES4,
                "activities": ACT_NAMES, "zones6": ZONES6, "poses": POSE, "arms": ARMS, "legs": LEGS,
                "count_max": N_MAX, "zone_count_max": 3, "seeds": a.seeds, "epochs": a.epochs, "windows": int(len(Xtr)),
                "seconds": round(time.time() - t0)}
        (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
        print("saved", out, meta)


if __name__ == "__main__":
    main()
