"""Test vectors for the C feature code (firmware/components/wsn_feat): real windows -> Python link_seq output.

The amplitude is quantised exactly like on the board (|H| from int8 I/Q, x32, rounded) and the Python features
are computed from that same quantised amplitude, so the C test checks the algorithm, not the input rounding.
Format (little endian): u32 n_cases; per case: u32 n, f32 t0, n x (f32 t, u16 amp[64]), f32 expected[40][4].

usage (wsn-ml image): python gen_feat_vectors.py --session 20261013-065019-45cd --out /fw/components/wsn_feat/test/vectors.bin
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

import numpy as np

from build_rich import LINKS, link_csi
from build_seq import link_seq
from wsn.keys import KeyStore


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="/data")
    ap.add_argument("--session", default="20261013-065019-45cd")
    ap.add_argument("--out", required=True)
    ap.add_argument("--per-link", type=int, default=12)
    a = ap.parse_args()
    csi = link_csi(Path(a.data) / "sessions" / a.session / "csi.bin", KeyStore(Path(a.data) / "device_keys.json"))
    rng = np.random.default_rng(1)
    cases = []
    for m in LINKS:
        if m not in csi:
            continue
        t, H = csi[m]
        amp_q = np.minimum(np.floor(np.abs(H) * 32 + 0.5), 65535).astype(np.uint16)
        for t0 in rng.uniform(t[0], t[-1] - 4.5, a.per_link):
            i0, i1 = np.searchsorted(t, [t0 - 0.5, t0 + 4.5])  # с краями: C сам выбирает кадры окна
            tt = (t[i0:i1] - t0).astype(np.float32)           # время относительно начала окна
            aq = amp_q[i0:i1]
            exp = link_seq(tt.astype(np.float64), (aq / 32.0).astype(np.complex64), 0.0)
            cases.append((tt, aq, exp))
    with open(a.out, "wb") as f:
        f.write(struct.pack("<I", len(cases)))
        for tt, aq, exp in cases:
            f.write(struct.pack("<If", len(tt), 0.0))
            for ti, ai in zip(tt, aq):
                f.write(struct.pack("<f", ti) + ai.astype("<u2").tobytes())
            f.write(exp.astype("<f4").tobytes())
    print("wrote", len(cases), "cases to", a.out)


if __name__ == "__main__":
    main()
