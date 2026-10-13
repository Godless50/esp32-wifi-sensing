"""E2 per-zone breakdown: activity accuracy of the pooled-feature model for each held-out point."""
import json

import numpy as np

import train_real as tr

d = np.load("/data/ml/rich_v2.npz")
links, feats, zones = list(d["links"]), list(d["feats"]), list(d["zones"])
sc = d["session"] == tr.SCRIPT
rate = d["X"][:, :, feats.index("rate")]
use = [m for i, m in enumerate(links) if (rate[sc & (d["act"] >= 0), i] > 0).mean() > 0.9]
Fl, Fp, rise = tr.prepare(d, use)
alive = (rate[:, [links.index(m) for m in use]] > 0).all(1)
act, zone = np.where(alive & sc, d["act"], -1), d["zone"]
y = np.where(act <= 1, 0, act - 1)
out = {}
for z in sorted(set(zone[act >= 1].tolist())):
    te = (act >= 1) & (zone == z)
    trn = (act >= 0) & ~te
    p = tr.models()["hgb"]().fit(Fp[trn], y[trn]).predict(Fp[te])
    out[zones[z]] = tr.confusion(y[te], p, tr.ACT3)
    # насколько линии вообще «видят» точку: средний подъём lvar на самой возмущённой линии
    out[zones[z]]["max_rise_median"] = {tr.ACT3[c]: round(float(np.median(rise[te & (y == c)].max(1))), 3)
                                        for c in range(3) if (te & (y == c)).any()}
out["rest_max_rise_median"] = round(float(np.median(rise[act == 0].max(1))), 3)
print(json.dumps(out, ensure_ascii=False, indent=1))
