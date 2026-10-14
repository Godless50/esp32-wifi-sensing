"""Сравнение моделей на отложенных записях (вне обучения): движение и активность, сбалансированная точность и ECE.

Каждая модель получает окна со своим набором линий: X берётся из датасета, где те же записи собраны на её линиях
(build_seq.py --links). Вариант "only:<mac>" обнуляет все линии, кроме указанной — так модель работает сейчас,
когда в эфире осталась одна линия.

usage: cd ml && PYTHONPATH=../server python compare_heldout.py \
         --case old:../data/ml/live_model_s2:../data/ml/heldout_v1.npz \
         --case old_now:../data/ml/live_model_s2:../data/ml/heldout_v1.npz:only:98:b3:ef:02:da:28 \
         --case ap:../data/ml/live_model_ap:../data/ml/heldout_ap.npz [--out ../data/eval/compare.json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from calib import report
from learner import ensemble, load_model
from nn_common import bal
from train_final import labels


def evaluate(model: str, data: str, only: str | None) -> dict:
    meta, nets = load_model(Path(model))
    d = np.load(data)
    X = d["X"].astype(np.float32)
    links = [str(m) for m in d["links"]]
    if links != list(meta["links"]):
        raise SystemExit(f"{data}: links {links} != model links {meta['links']}")
    if only:
        keep = links.index(only)
        X = X.copy()
        X[:, [i for i in range(len(links)) if i != keep]] = 0.0
    ya, _, ym = labels(d)
    pa, _, pm = ensemble(nets, X)
    a, m = ya >= 0, ym >= 0
    still = m & (ym == 0)
    return {
        "model": model, "version": meta["version"], "links": len(links), "only": only,
        "trained_on_overlap": sorted(set(np.unique(d["session"]).tolist()) & set(meta.get("trained_on", []))),
        "moving": {**report(pm[m], ym[m]), "balanced_acc": round(float(bal(ym[m], (pm[m] > 0.5).astype(int))), 4),
                   "false_moving_on_still": round(float((pm[still] > 0.5).mean()), 4) if still.any() else None},
        "activity": {**report(pa[a], ya[a]), "balanced_acc": round(float(bal(ya[a], pa[a].argmax(1))), 4)},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", action="append", required=True, help="name:model_dir:dataset[:only:<mac>]")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    res = {}
    for c in a.case:
        name, model, data, *rest = c.split(":", 3)
        only = rest[0][len("only:"):] if rest and rest[0].startswith("only:") else None
        res[name] = evaluate(model, data, only)
        r = res[name]
        print(f"{name:>8}: moving bal {r['moving']['balanced_acc']} false-on-still {r['moving']['false_moving_on_still']} "
              f"ECE {r['moving']['ece']} | activity acc {r['activity']['acc']} bal {r['activity']['balanced_acc']} "
              f"ECE {r['activity']['ece']} (n {r['activity']['n']}) | overlap {r['trained_on_overlap']}", flush=True)
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1, ensure_ascii=False))
        print("->", a.out)


if __name__ == "__main__":
    main()
