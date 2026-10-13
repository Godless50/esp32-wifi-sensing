"""Калибровка модели learner на отложенных размеченных сессиях: Brier/ECE как есть и с температурой.

Честная проверка температуры: подбор на одной отложенной сессии, оценка на другой (и наоборот) — так видно,
переносится ли поправка на новую запись, а не только на ту, где её подбирали.

usage: cd ml && PYTHONPATH=../server ../.venv-rocm/bin/python calib_eval.py [--model ../data/ml/live_model_s2]
       [--anchor ../data/ml/seq_v1.npz] [--out ../data/eval/calib-<version>.json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from calib import apply_temperature, fit_temperature, report
from learner import VAL_SESSIONS, ensemble, load_model
from train_final import labels


HEADS = ("activity", "zone", "moving")


def heads(nets, X, ya, yz, ym):
    """Per head (probabilities, labels) on the labelled windows; an empty session gives empty arrays."""
    if len(X) == 0:
        return {h: (np.zeros((0, 2)), np.zeros(0, int)) for h in HEADS}
    pa, pz, pm = ensemble(nets, X)
    a, z, m = ya >= 0, yz >= 0, ym >= 0
    return {"activity": (pa[a], ya[a]), "zone": (pz[z], yz[z]), "moving": (pm[m], ym[m])}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="../data/ml/live_model_s2")
    ap.add_argument("--anchor", default="../data/ml/seq_v1.npz")
    ap.add_argument("--out", default="")
    ap.add_argument("--sessions", default=",".join(VAL_SESSIONS),
                    help="comma list; sessions NOT in the model's trained_on give an honest number")
    a = ap.parse_args()
    meta, nets = load_model(Path(a.model))
    d = np.load(a.anchor)
    X = d["X"].astype(np.float32)
    ya, yz, ym = labels(d)
    sess = d["session"]
    sessions = tuple(s for s in a.sessions.split(",") if s)
    seen = sorted(set(sessions) & set(meta.get("trained_on", [])))
    out = {"model": a.model, "version": meta["version"], "sessions": list(sessions),
           "in_training_set": seen, "honest": not seen, "heads": {}}
    per = {}
    for s in sessions:
        m = (sess == s) & ((ya >= 0) | (yz >= 0) | (ym >= 0))
        per[s] = heads(nets, X[m], ya[m], yz[m], ym[m])
    for h in HEADS:
        parts = [per[s][h] for s in sessions if len(per[s][h][1])]
        if not parts:
            out["heads"][h] = {"n": 0}
            continue
        p_all = np.concatenate([q for q, _ in parts])
        y_all = np.concatenate([y for _, y in parts])
        t_in = fit_temperature(p_all, y_all)
        r = {"raw": report(p_all, y_all), "temperature_in_sample": round(t_in, 3),
             "with_T_in_sample": report(apply_temperature(p_all, t_in), y_all), "cross_session": {}}
        for test_s in sessions:  # температура по остальным сессиям -> проверка на этой
            rest = [s for s in sessions if s != test_s and len(per[s][h][1])]
            pt, yt = per[test_s][h]
            if not rest or not len(yt):
                continue
            t = fit_temperature(np.concatenate([per[s][h][0] for s in rest]), np.concatenate([per[s][h][1] for s in rest]))
            r["cross_session"][f"fit {'+'.join(rest)} -> test {test_s}"] = {
                "T": round(t, 3), "raw": report(pt, yt), "with_T": report(apply_temperature(pt, t), yt)}
        out["heads"][h] = r
    txt = json.dumps(out, indent=1, ensure_ascii=False)
    print(txt)
    path = Path(a.out) if a.out else Path("../data/eval") / f"calib-{meta['version']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(txt)
    print(f"-> {path}")


if __name__ == "__main__":
    main()
