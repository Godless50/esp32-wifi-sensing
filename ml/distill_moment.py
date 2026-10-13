"""Distil MOMENT-1-small (35 M parameters, time-series foundation model) into our small per-link CNN.

1. Teacher features: every link's 4 channels (build_seq.py, 40 steps) are resampled to 512 points and embedded by
   frozen MOMENT; the 4 channel embeddings of a link are averaged -> [10 links, 512] per window. Computed for the
   original window and K augmented copies (same augmentation as train_nn.py).
2. Teacher head: a small MLP on the frozen embeddings (activity from mean/max over links, zone from all links).
3. Student: train_nn_torch.Net trained on the same copies with labels + KL to the teacher's soft labels (T=2)
   + its per-link embedding regressed onto the teacher's (linear adapter 32 -> 512).
Every step runs per leave-one-session-out fold, so neither teacher head nor student sees the test recording.

usage: HSA_OVERRIDE_GFX_VERSION=10.3.0 .venv-rocm/bin/python ml/distill_moment.py --data data/ml/seq_v1.npz
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from nn_common import ACT_NAMES, SCRIPTED, act_class, augment, bal, confusion, squash
from train_nn_torch import DEV, Net

ZN = ["зал", "кухня", "коридор", "санузел"]


@torch.no_grad()
def teacher_embed(moment, X, bs=2048):
    """X [N, L, T, C] -> [N, L, 512] (mean over channels of MOMENT embeddings); silent links -> zeros."""
    N, L, T, C = X.shape
    s = torch.tensor(X, dtype=torch.float32).permute(0, 1, 3, 2).reshape(-1, 1, T)  # [N*L*C, 1, T]
    s = F.interpolate(s, size=512, mode="linear", align_corners=True)
    out = torch.empty(len(s), 512, dtype=torch.float16)
    for i in range(0, len(s), bs):
        out[i:i + bs] = moment(x_enc=s[i:i + bs].to(DEV)).embeddings.half().cpu()
    e = out.reshape(N, L, C, 512).float().mean(2)
    alive = torch.tensor(np.abs(X).sum((2, 3)) > 0)
    return (e * alive[..., None]).numpy().astype(np.float16)


class Head(nn.Module):
    def __init__(self, L, D=512):
        super().__init__()
        self.act = nn.Sequential(nn.Linear(2 * D, 128), nn.ReLU(), nn.Dropout(0.3), nn.Linear(128, 4))
        self.zone = nn.Sequential(nn.Linear(L * D, 128), nn.ReLU(), nn.Dropout(0.3), nn.Linear(128, 4))

    def forward(self, e):  # e [B, L, D]
        return self.act(torch.cat([e.max(1).values, e.mean(1)], 1)), self.zone(e.reshape(len(e), -1))


def masked_ce(logits, y, w=None):
    m = y >= 0
    if not m.any():
        return logits.sum() * 0
    l = F.cross_entropy(logits[m], y[m], weight=w)
    return l


def class_weights(y, n=4):
    c = np.array([(y == k).sum() for k in range(n)], float)
    return torch.tensor(np.where(c > 0, c.sum() / (n * np.maximum(c, 1)), 0), dtype=torch.float32, device=DEV)


def student_forward(net, x):
    B, L, T, C = x.shape
    e = net.enc(x.reshape(B * L, T, C).transpose(1, 2)).reshape(B, L, -1)
    h = net.trunk(torch.cat([e.max(1).values, e.mean(1)], 1))
    return net.act(h), net.zone(e.reshape(B, -1)), e


def train_head(E, ya, yz, epochs=60, seed=0):
    torch.manual_seed(seed)
    h = Head(E.shape[1]).to(DEV)
    opt = torch.optim.AdamW(h.parameters(), 1e-3, weight_decay=1e-2)
    Et = torch.tensor(E, dtype=torch.float32, device=DEV)
    ta, tz = (torch.tensor(np.asarray(v, np.int64), device=DEV) for v in (ya, yz))
    wa = class_weights(ya)
    for _ in range(epochs):
        h.train()
        for idx in torch.randperm(len(Et), device=DEV).split(128):
            pa, pz = h(Et[idx])
            loss = masked_ce(pa, ta[idx], wa) + masked_ce(pz, tz[idx])
            opt.zero_grad(); loss.backward(); opt.step()
    return h.eval()


def train_student(Xc, Ec, ya, yz, soft_a, soft_z, epochs=60, seed=0, T=2.0, alpha=0.5, beta=0.1):
    torch.manual_seed(seed)
    net = Net(Xc.shape[1], Xc.shape[3]).to(DEV)
    adapt = nn.Linear(32, 512).to(DEV)
    opt = torch.optim.AdamW(list(net.parameters()) + list(adapt.parameters()), 2e-3, weight_decay=1e-3)
    X = torch.tensor(squash(Xc), dtype=torch.float32, device=DEV)
    E = torch.tensor(Ec, dtype=torch.float32, device=DEV)
    E = E / (E.norm(dim=-1, keepdim=True) + 1e-6)
    ta, tz = (torch.tensor(np.asarray(v, np.int64), device=DEV) for v in (ya, yz))
    sa, sz = (torch.tensor(v, dtype=torch.float32, device=DEV) for v in (soft_a, soft_z))
    wa = class_weights(ya)
    alive = (E.abs().sum(-1) > 0).float()
    for _ in range(epochs):
        net.train()
        for idx in torch.randperm(len(X), device=DEV).split(64):
            pa, pz, e = student_forward(net, X[idx])
            hard = masked_ce(pa, ta[idx], wa) + masked_ce(pz, tz[idx])
            kd = F.kl_div(F.log_softmax(pa / T, 1), sa[idx], reduction="batchmean") * T * T
            mz = tz[idx] >= 0
            if mz.any():
                kd = kd + F.kl_div(F.log_softmax(pz[mz] / T, 1), sz[idx][mz], reduction="batchmean") * T * T
            pe = adapt(e)
            pe = pe / (pe.norm(dim=-1, keepdim=True) + 1e-6)
            emb = (((pe - E[idx]) ** 2).sum(-1) * alive[idx]).sum() / alive[idx].sum().clamp(min=1)
            loss = (1 - alpha) * hard + alpha * kd + beta * emb
            opt.zero_grad(); loss.backward(); opt.step()
    return net.eval()


@torch.no_grad()
def evaluate(pa, pz, ya, yz):
    pa, pz = pa.argmax(1).cpu().numpy(), pz.argmax(1).cpu().numpy()
    z = yz >= 0
    return {"activity_bal_acc": bal(ya, pa), "zone_bal_acc": bal(yz[z], pz[z]),
            "moving_bal_acc": bal((ya > 0).astype(int), (pa > 0).astype(int)),
            "activity_confusion": confusion(ya, pa, ACT_NAMES), "zone_confusion": confusion(yz[z], pz[z], ZN)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/ml/seq_v1.npz")
    ap.add_argument("--out", default="data/ml/distill_v1")
    ap.add_argument("--copies", type=int, default=3)
    ap.add_argument("--epochs", type=int, default=60)
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    d = np.load(a.data)
    sess, act, zone = d["session"], d["act"], d["zone"]
    keep = np.isin(sess, SCRIPTED) & (act >= 0)  # учитель и ученик учатся на точных метках сценариев
    X, sess, act, zone = d["X"][keep].astype(np.float32), sess[keep], act[keep], zone[keep]
    ya = act_class(act)
    yz = np.array([{1: 0, 2: 1, 3: 2, 4: 3}.get(int(z), -1) if a_ >= 1 else -1 for z, a_ in zip(zone, act)])
    rng = np.random.default_rng(0)
    Xc = np.concatenate([X] + [augment(X, rng, ya, yz) for _ in range(a.copies)])
    rep_idx = np.tile(np.arange(len(X)), a.copies + 1)
    t0 = time.time()
    cache = out / f"teacher_emb_{a.copies}.npy"
    if cache.exists():
        Ec = np.load(cache)
    else:
        from momentfm import MOMENTPipeline
        moment = MOMENTPipeline.from_pretrained("AutonLab/MOMENT-1-small", model_kwargs={"task_name": "embedding"})
        moment.init()
        moment = moment.to(DEV).eval()
        Ec = teacher_embed(moment, Xc)
        np.save(cache, Ec)
        del moment
        torch.cuda.empty_cache()
    print(f"teacher embeddings {Ec.shape} in {time.time() - t0:.0f}s", flush=True)
    rep = {"copies": a.copies, "epochs": a.epochs, "windows": int(len(X)), "loso": {}}
    for test in SCRIPTED:
        tr = sess[rep_idx] != test
        te = sess == test
        head = train_head(Ec[tr], ya[rep_idx][tr], yz[rep_idx][tr], epochs=a.epochs)
        with torch.no_grad():
            Et = torch.tensor(Ec[tr], dtype=torch.float32, device=DEV)
            la, lz = head(Et)
            soft_a, soft_z = (F.softmax(l / 2.0, 1).cpu().numpy() for l in (la, lz))
            ta, tz = head(torch.tensor(Ec[:len(X)][te], dtype=torch.float32, device=DEV))
        r = {"teacher": evaluate(ta, tz, ya[te], yz[te])}
        stu = train_student(Xc[tr], Ec[tr], ya[rep_idx][tr], yz[rep_idx][tr], soft_a, soft_z, epochs=a.epochs)
        with torch.no_grad():
            sa, sz, _ = student_forward(stu, torch.tensor(squash(X[te]), dtype=torch.float32, device=DEV))
        r["student"] = evaluate(sa, sz, ya[te], yz[te])
        rep["loso"][test] = r
        print(test, {who: {k: v for k, v in m.items() if "confusion" not in k} for who, m in r.items()},
              f"{time.time() - t0:.0f}s", flush=True)
    rep["student_params"] = sum(p.numel() for p in stu.parameters())
    (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    print("done", rep["student_params"], "student params")


if __name__ == "__main__":
    main()
