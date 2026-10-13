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


def heads(nets, X, ya, yz):
    pa, pz, _ = ensemble(nets, X)
    z = yz >= 0
    return {"activity": (pa, ya), "zone": (pz[z], yz[z])}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="../data/ml/live_model_s2")
    ap.add_argument("--anchor", default="../data/ml/seq_v1.npz")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    meta, nets = load_model(Path(a.model))
    d = np.load(a.anchor)
    X = d["X"].astype(np.float32)
    ya, yz, _ = labels(d)
    sess = d["session"]
    out = {"model": a.model, "version": meta["version"], "sessions": list(VAL_SESSIONS), "heads": {}}
    per = {}
    for s in VAL_SESSIONS:
        m = (sess == s) & (ya >= 0)
        per[s] = heads(nets, X[m], ya[m], yz[m])
    for h in ("activity", "zone"):
        p_all = np.concatenate([per[s][h][0] for s in VAL_SESSIONS])
        y_all = np.concatenate([per[s][h][1] for s in VAL_SESSIONS])
        t_in = fit_temperature(p_all, y_all)
        r = {"raw": report(p_all, y_all), "temperature_in_sample": round(t_in, 3),
             "with_T_in_sample": report(apply_temperature(p_all, t_in), y_all), "cross_session": {}}
        for fit_s, test_s in (VAL_SESSIONS, VAL_SESSIONS[::-1]):
            t = fit_temperature(*per[fit_s][h])
            pt, yt = per[test_s][h]
            r["cross_session"][f"fit {fit_s} -> test {test_s}"] = {
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
