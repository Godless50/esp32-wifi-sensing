"""TensorFlow side of the board export: Keras twin of train_nn_torch.Net -> int8 TFLite -> board model blob.

1. Keras model with the same layers, weights copied from the torch net (export_weights.py); float outputs are
   checked against torch on every window of seq_v1 (max abs difference must be tiny).
2. Full-integer quantisation (int8 in/out) with a representative set of real windows; agreement of the int8
   model with float on the scripted windows is reported (activity / zone / moving argmax).
3. Board blob = 128-byte header + .tflite, written for the model_a/model_b flash partitions:
   'WSNM' u32 version, u32 n_links, u32 tflite_len, u32 crc32(tflite), 16 x 6-byte link MACs (in input order).

usage (tensorflow image): python export_tflite.py --model /data/ml/live_model_v2 --net 0
"""

from __future__ import annotations

import argparse
import json
import struct
import zlib
from pathlib import Path

import numpy as np
import tensorflow as tf

L, T, C, W = 10, 40, 4, 32
SCRIPTED = ["20261013-053501-ee64", "20261013-065019-45cd"]


def squash(x):
    return np.sign(x) * np.log1p(np.abs(x))


def build(arch="s2"):
    inp = tf.keras.Input((L, T, C), batch_size=1, name="csi")
    x = tf.keras.layers.Lambda(lambda t: tf.reshape(t, [L, T, C]), name="links_as_batch")(inp)
    x = tf.keras.layers.Conv1D(24, 5, padding="same", activation="relu", name="c1")(x)
    if arch == "dil":
        x = tf.keras.layers.Conv1D(W, 5, padding="same", dilation_rate=2, activation="relu", name="c2")(x)
        x = tf.keras.layers.Conv1D(W, 3, padding="same", dilation_rate=4, activation="relu", name="c3")(x)
    else:  # как torch padding=2: по 2 нуля с обеих сторон (у Keras "same" при шаге 2 отступы несимметричны)
        x = tf.keras.layers.ZeroPadding1D(2)(x)
        x = tf.keras.layers.Conv1D(W, 5, strides=2, padding="valid", activation="relu", name="c2")(x)
        x = tf.keras.layers.Conv1D(W, 5, padding="same", activation="relu", name="c3")(x)
    x = tf.keras.layers.GlobalAveragePooling1D(name="gap")(x)                       # [L, W]
    e = tf.keras.layers.Lambda(lambda t: tf.reshape(t, [1, L, W]), name="emb")(x)
    z = tf.keras.layers.Dense(64, activation="relu", name="z1")(tf.keras.layers.Flatten()(e))
    z = tf.keras.layers.Dense(4, name="zone")(z)
    mx = tf.keras.layers.Lambda(lambda t: tf.reduce_max(t, axis=1), name="lmax")(e)
    mn = tf.keras.layers.Lambda(lambda t: tf.reduce_mean(t, axis=1), name="lmean")(e)
    h = tf.keras.layers.Dense(48, activation="relu", name="t1")(tf.keras.layers.Concatenate()([mx, mn]))
    return tf.keras.Model(inp, [tf.keras.layers.Dense(4, name="act")(h), z, tf.keras.layers.Dense(1, name="move")(h)])


def load_weights(m, w):
    conv = lambda k: np.transpose(w[f"enc_{k}_weight"], (2, 1, 0))  # torch [out,in,k] -> keras [k,in,out]
    m.get_layer("c1").set_weights([conv(0), w["enc_0_bias"]])
    m.get_layer("c2").set_weights([conv(2), w["enc_2_bias"]])
    m.get_layer("c3").set_weights([conv(4), w["enc_4_bias"]])
    dense = lambda name, key: m.get_layer(name).set_weights([w[f"{key}_weight"].T, w[f"{key}_bias"]])
    dense("z1", "zone_0"); dense("zone", "zone_3"); dense("t1", "trunk_0"); dense("act", "act"); dense("move", "move")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="/data/ml/live_model_v2")
    ap.add_argument("--net", type=int, default=0)
    ap.add_argument("--data", default="/data/ml/seq_v1.npz")
    a = ap.parse_args()
    md = Path(a.model)
    meta = json.loads((md / "meta.json").read_text(encoding="utf-8"))
    w = dict(np.load(md / f"net{a.net}_numpy.npz"))
    d = np.load(a.data)
    X = squash(d["X"].astype(np.float32))
    m = build(meta.get("arch", "dil"))
    load_weights(m, w)
    rng = np.random.default_rng(0)
    idx = rng.choice(len(X), 200, replace=False)
    diff = 0.0
    for i in idx:
        out = m(X[i:i + 1], training=False)
        for o, ref in zip(out, (w["ref_act"], w["ref_zone"], w["ref_move"])):
            diff = max(diff, float(np.abs(o.numpy().reshape(-1) - ref[i].reshape(-1)).max()))
    print(f"keras vs torch: max abs diff {diff:.2e}", flush=True)
    assert diff < 1e-3, diff

    def rep():
        for i in rng.choice(len(X), 400, replace=False):
            yield [X[i:i + 1]]
    conv = tf.lite.TFLiteConverter.from_keras_model(m)
    conv.optimizations = [tf.lite.Optimize.DEFAULT]
    conv.representative_dataset = rep
    conv.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    conv.inference_input_type = tf.int8
    conv.inference_output_type = tf.int8
    tfl = conv.convert()
    (md / "model_int8.tflite").write_bytes(tfl)

    it = tf.lite.Interpreter(model_content=tfl)
    it.allocate_tensors()
    ind = it.get_input_details()[0]
    od = it.get_output_details()  # порядок выходов = порядок в прошивке (output(i) в TFLM)
    print("ops:", sorted({op["op_name"] for op in it._get_ops_details()}), flush=True)
    s_in, z_in = ind["quantization"]
    # какой выход что значит: сверяем с выходами по именам через signature runner
    q0 = np.clip(np.round(X[:1] / s_in + z_in), -128, 127).astype(np.int8)
    it.set_tensor(ind["index"], q0); it.invoke()
    by_pos = [it.get_tensor(o["index"]).copy() for o in od]
    sig = it.get_signature_runner()(**{it.get_signature_list()["serving_default"]["inputs"][0]: q0})
    order = {}
    for name, v in sig.items():
        key = ("act", "zone", "move")[int(name.rsplit("_", 1)[-1])]  # output_0..2 = порядок выходов Keras-модели
        order[key] = next(p for p, b in enumerate(by_pos) if b.shape == v.shape and np.array_equal(b, v) and p not in order.values())
    print("output positions:", order, flush=True)
    sc = np.flatnonzero(np.isin(d["session"], SCRIPTED) & (d["act"] >= 0))
    agree = {"act": 0, "zone": 0, "move": 0}
    for i in sc:
        q = np.clip(np.round(X[i:i + 1] / s_in + z_in), -128, 127).astype(np.int8)
        it.set_tensor(ind["index"], q)
        it.invoke()
        got = {}
        for k, p in order.items():
            s_o, z_o = od[p]["quantization"]
            got[k] = (it.get_tensor(od[p]["index"]).astype(np.float32).reshape(-1) - z_o) * s_o
        agree["act"] += int(got["act"].argmax() == w["ref_act"][i].argmax())
        agree["zone"] += int(got["zone"].argmax() == w["ref_zone"][i].argmax())
        agree["move"] += int((got["move"][0] > 0) == (w["ref_move"][i] > 0))
    agree = {k: round(v / len(sc), 4) for k, v in agree.items()}
    print("int8 vs float agreement on scripted windows:", agree, flush=True)
    links = [bytes.fromhex(x.replace(":", "")) for x in meta["links"]]
    ver = int(meta["version"]) & 0xFFFFFFFF
    hdr = b"WSNM" + struct.pack("<IIII", ver, len(links), len(tfl), zlib.crc32(tfl))
    hdr += b"".join(links) + b"\x00" * 6 * (16 - len(links))
    hdr += bytes([order["act"], order["zone"], order["move"]])  # позиции выходов act / zone / move (offset 116)
    hdr += b"\x00" * (128 - len(hdr))
    (md / "board_model.bin").write_bytes(hdr + tfl)
    io = {"input_scale": float(s_in), "input_zero": int(z_in), "order": order, "outputs": [
        {"name": o["name"], "shape": [int(x) for x in o["shape"]], "scale": float(o["quantization"][0]),
         "zero": int(o["quantization"][1])} for o in it.get_output_details()],
        "tflite_bytes": len(tfl), "agreement": agree, "version": ver}
    (md / "board_model.json").write_text(json.dumps(io, ensure_ascii=False, indent=1))
    print("board blob", md / "board_model.bin", len(hdr) + len(tfl), "bytes", io["outputs"])


if __name__ == "__main__":
    main()
