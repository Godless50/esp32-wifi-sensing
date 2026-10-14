package ru.wsn.capture.core

import kotlin.math.cos
import kotlin.math.exp
import kotlin.math.max
import kotlin.math.sqrt

/** On-phone training of the room model: one MLP by AdamW with the partial-label (allowed-zone-set) softmax loss
 *  and the moving sigmoid head — the same math as server/wsn/train.fit_net. The deterministic core (forward,
 *  gradients, AdamW) is checked against a Python reference; the RNG pieces (init, shuffle, link dropout, input
 *  noise) go through TrainRng so the test can make them deterministic and production can seed them. */
object Trainer {
    private const val NF = ZoneFeatures.NF

    data class FitConfig(val hidden: Pair<Int, Int> = 64 to 32, val epochs: Int = 80, val batch: Int = 128,
                         val lr: Double = 3e-3, val weightDecay: Double = 1e-4, val linkDrop: Double = 0.15,
                         val noise: Double = 0.05, val moveW: Double = 0.5)

    interface TrainRng {
        fun shuffle(a: IntArray)
        fun dropout(rows: Int, links: Int, p: Double): Array<BooleanArray>
        fun noise(rows: Int, cols: Int, sd: Double): Array<DoubleArray>
    }

    /** Weights of one net: matrices W1,W2,Wz,Wm and biases b1,b2,bz,bm. */
    class Net(
        val w1: Array<DoubleArray>, val b1: DoubleArray, val w2: Array<DoubleArray>, val b2: DoubleArray,
        val wz: Array<DoubleArray>, val bz: DoubleArray, val wm: Array<DoubleArray>, val bm: DoubleArray,
    ) {
        fun copy() = Net(w1.deep(), b1.copyOf(), w2.deep(), b2.copyOf(), wz.deep(), bz.copyOf(), wm.deep(), bm.copyOf())
    }

    // ---- small linear algebra (dense, double) ----
    private fun Array<DoubleArray>.deep() = Array(size) { this[it].copyOf() }
    private fun mm(a: Array<DoubleArray>, b: Array<DoubleArray>): Array<DoubleArray> {
        val n = a.size; val k = b.size; val m = b[0].size
        return Array(n) { i -> DoubleArray(m).also { o -> for (t in 0 until k) { val av = a[i][t]; if (av != 0.0) { val br = b[t]; for (j in 0 until m) o[j] += av * br[j] } } } }
    }
    private fun mmAtB(a: Array<DoubleArray>, b: Array<DoubleArray>): Array<DoubleArray> { // a^T b
        val n = a.size; val k = a[0].size; val m = b[0].size
        val out = Array(k) { DoubleArray(m) }
        for (i in 0 until n) { val ar = a[i]; val br = b[i]; for (t in 0 until k) { val av = ar[t]; if (av != 0.0) for (j in 0 until m) out[t][j] += av * br[j] } }
        return out
    }
    private fun mmABt(a: Array<DoubleArray>, b: Array<DoubleArray>): Array<DoubleArray> { // a b^T
        val n = a.size; val m = b.size; val k = a[0].size
        return Array(n) { i -> DoubleArray(m) { j -> var s = 0.0; for (t in 0 until k) s += a[i][t] * b[j][t]; s } }
    }
    private fun colSums(a: Array<DoubleArray>): DoubleArray { val o = DoubleArray(a[0].size); for (r in a) for (j in r.indices) o[j] += r[j]; return o }

    fun standardisation(x: Array<Array<DoubleArray>>): Pair<Array<DoubleArray>, Array<DoubleArray>> {
        val l = x[0].size
        val mu = Array(l) { DoubleArray(NF) }
        val sd = Array(l) { DoubleArray(NF) { 1.0 } }
        for (j in 0 until l) {
            val live = x.indices.filter { x[it][j][NF - 1] > 0.5 }
            if (live.size >= 2) for (f in 0 until NF - 1) {
                var m = 0.0; for (n in live) m += x[n][j][f]; m /= live.size
                var v = 0.0; for (n in live) { val d = x[n][j][f] - m; v += d * d }; v = sqrt(v / live.size)
                mu[j][f] = m; sd[j][f] = max(v, 0.05)
            }
        }
        return mu to sd
    }

    /** Raw features [n][L][NF] -> standardised net input [n][L*NF] (dead link -> zeros + alive 0). */
    private fun standardize(x: Array<Array<DoubleArray>>, mu: Array<DoubleArray>, sd: Array<DoubleArray>): Array<DoubleArray> {
        val l = x[0].size
        return Array(x.size) { n ->
            DoubleArray(l * NF).also { z ->
                for (j in 0 until l) {
                    val alive = x[n][j][NF - 1] > 0.5
                    for (f in 0 until NF - 1) z[j * NF + f] = if (alive) (x[n][j][f] - mu[j][f]) / sd[j][f] else 0.0
                    z[j * NF + NF - 1] = if (alive) 1.0 else 0.0
                }
            }
        }
    }

    fun sampleWeights(z: Array<DoubleArray>): DoubleArray {
        val n = z.size; val k = z[0].size
        val sum = DoubleArray(n) { z[it].sum() }
        val single = BooleanArray(n) { sum[it] == 1.0 }
        val w = DoubleArray(n) { if (sum[it] > 0) 0.5 else 0.0 }
        val singleCount = single.count { it }
        if (singleCount > 0) {
            val cls = IntArray(n) { if (single[it]) z[it].indexOfFirst { v -> v == z[it].max() } else -1 }
            val counts = DoubleArray(k); for (i in 0 until n) if (single[i]) counts[cls[i]] += 1.0
            val kPos = counts.count { it > 0 }
            for (i in 0 until n) if (single[i]) {
                val cw = if (counts[cls[i]] > 0) singleCount.toDouble() / (kPos * max(counts[cls[i]], 1.0)) else 0.0
                w[i] = cw.coerceIn(0.2, 5.0)
            }
        }
        return w
    }

    private fun relu(a: Array<DoubleArray>) = Array(a.size) { i -> DoubleArray(a[i].size) { if (a[i][it] > 0) a[i][it] else 0.0 } }
    private fun softmaxRows(a: Array<DoubleArray>) = Array(a.size) { i ->
        var m = a[i][0]; for (v in a[i]) if (v > m) m = v
        var s = 0.0; val e = DoubleArray(a[i].size) { exp(a[i][it] - m).also { v -> s += v } }
        DoubleArray(e.size) { e[it] / s }
    }
    private fun sig(x: Double) = 1.0 / (1.0 + exp(-x.coerceIn(-30.0, 30.0)))

    fun fitNet(x: Array<Array<DoubleArray>>, z: Array<DoubleArray>, mAxis: DoubleArray,
               mu: Array<DoubleArray>, sd: Array<DoubleArray>, init: Net, cfg: FitConfig, rng: TrainRng): Net {
        val n = x.size; val l = x[0].size; val k = z[0].size
        val net = init.copy()
        val mats = linkedMapOf("W1" to net.w1, "W2" to net.w2, "Wz" to net.wz, "Wm" to net.wm)
        val vecs = linkedMapOf("b1" to net.b1, "b2" to net.b2, "bz" to net.bz, "bm" to net.bm)
        fun zerosLike(a: Array<DoubleArray>) = Array(a.size) { DoubleArray(a[0].size) }
        val m1m = mats.mapValues { zerosLike(it.value) }; val m2m = mats.mapValues { zerosLike(it.value) }
        val m1v = vecs.mapValues { DoubleArray(it.value.size) }; val m2v = vecs.mapValues { DoubleArray(it.value.size) }
        val wz = sampleWeights(z)
        val wm = DoubleArray(n) { if (mAxis[it] >= 0) 1.0 else 0.0 }
        var use = (0 until n).filter { wz[it] > 0 || wm[it] > 0 }.toIntArray()
        var step = 0
        for (ep in 0 until cfg.epochs) {
            val lr = cfg.lr * (0.5 * (1 + cos(Math.PI * ep / cfg.epochs)))
            rng.shuffle(use)
            var b0 = 0
            while (b0 < use.size) {
                val idx = use.copyOfRange(b0, minOf(b0 + cfg.batch, use.size)); b0 += cfg.batch
                val nb0 = idx.size
                val xb = Array(nb0) { i -> Array(l) { j -> x[idx[i]][j].copyOf() } }
                if (cfg.linkDrop > 0 && l > 1) { val dead = rng.dropout(nb0, l, cfg.linkDrop); for (i in 0 until nb0) for (j in 0 until l) if (dead[i][j]) xb[i][j] = DoubleArray(NF) }
                val zs = standardize(xb, mu, sd)
                if (cfg.noise > 0) { val ns = rng.noise(nb0, zs[0].size, cfg.noise); for (i in zs.indices) for (c in zs[i].indices) zs[i][c] += ns[i][c] }
                val a1 = mm(zs, net.w1).also { for (r in it) for (c in r.indices) r[c] += net.b1[c] }
                val r1 = relu(a1)
                val a2 = mm(r1, net.w2).also { for (r in it) for (c in r.indices) r[c] += net.b2[c] }
                val r2 = relu(a2)
                val lz = mm(r2, net.wz).also { for (r in it) for (c in r.indices) r[c] += net.bz[c] }
                val lmArr = mm(r2, net.wm).also { for (r in it) r[0] += net.bm[0] }
                val p = softmaxRows(lz)
                val nbw = max(idx.indices.sumOf { wz[idx[it]] + cfg.moveW * wm[idx[it]] }, 1e-9)
                val dlz = Array(nb0) { i ->
                    val t = z[idx[i]]; var s = 0.0; for (c in 0 until k) s += p[i][c] * t[c]; s = max(s, 1e-12)
                    DoubleArray(k) { c -> (p[i][c] - p[i][c] * t[c] / s) * wz[idx[i]] / nbw }
                }
                val dlm = Array(nb0) { i -> doubleArrayOf((sig(lmArr[i][0]) - mAxis[idx[i]].coerceIn(0.0, 1.0)) * wm[idx[i]] * cfg.moveW / nbw) }
                val gWz = mmAtB(r2, dlz); val gbz = colSums(dlz)
                val gWm = mmAtB(r2, dlm); val gbm = colSums(dlm)
                val d2 = run { val fromZ = mmABt(dlz, net.wz); val fromM = mmABt(dlm, net.wm)
                    Array(nb0) { i -> DoubleArray(net.wz.size) { h -> if (a2[i][h] > 0) fromZ[i][h] + fromM[i][h] else 0.0 } } }
                val gW2 = mmAtB(r1, d2); val gb2 = colSums(d2)
                val d1 = run { val fw = mmABt(d2, net.w2); Array(nb0) { i -> DoubleArray(net.w1[0].size) { h -> if (a1[i][h] > 0) fw[i][h] else 0.0 } } }
                val gW1 = mmAtB(zs, d1); val gb1 = colSums(d1)
                step++
                val gMats = mapOf("W1" to gW1, "W2" to gW2, "Wz" to gWz, "Wm" to gWm)
                val gVecs = mapOf("b1" to gb1, "b2" to gb2, "bz" to gbz, "bm" to gbm)
                val bc1 = 1 - Math.pow(0.9, step.toDouble()); val bc2 = 1 - Math.pow(0.999, step.toDouble())
                for ((name, w) in mats) { val g = gMats[name]!!; val a = m1m[name]!!; val v = m2m[name]!!
                    for (i in w.indices) for (j in w[i].indices) {
                        a[i][j] = 0.9 * a[i][j] + 0.1 * g[i][j]; v[i][j] = 0.999 * v[i][j] + 0.001 * g[i][j] * g[i][j]
                        val upd = (a[i][j] / bc1) / (sqrt(v[i][j] / bc2) + 1e-8)
                        w[i][j] -= lr * (upd + cfg.weightDecay * w[i][j])
                    } }
                for ((name, w) in vecs) { val g = gVecs[name]!!; val a = m1v[name]!!; val v = m2v[name]!!
                    for (j in w.indices) {
                        a[j] = 0.9 * a[j] + 0.1 * g[j]; v[j] = 0.999 * v[j] + 0.001 * g[j] * g[j]
                        w[j] -= lr * (a[j] / bc1) / (sqrt(v[j] / bc2) + 1e-8)
                    } }
            }
        }
        return net
    }
}
