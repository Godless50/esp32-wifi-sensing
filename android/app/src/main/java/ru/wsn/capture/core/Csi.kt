package ru.wsn.capture.core

import kotlin.math.abs
import kotlin.math.sqrt

/** CSI amplitude decode and per-link window statistics — the phone side of live.ltf_amplitude and
 *  zonemodel.link_stats. Same math as the server, checked by golden vectors. */
object Csi {
    const val WIN_S = 4.0
    const val BIN_S = 0.25
    const val MIN_FRAMES = 8
    const val MIN_LIVE_SC = 8
    const val NULL_AMP = 1.0
    // ESP-IDF LTF subcarrier order 0..31 then -32..-1; reorder to -32..31 => [32..63, 0..31]
    private val REORDER = IntArray(64) { if (it < 32) 32 + it else it - 32 }

    /** 64-subcarrier amplitude from one LTF. data is 128/256/384 bytes of int8 (imag, real) pairs. */
    fun ltfAmplitude(data: ByteArray, part: String): DoubleArray? {
        val n = data.size
        if (n != 128 && n != 256 && n != 384) return null
        val off = when (part) {
            "lltf" -> 0
            "ht" -> if (n < 256) return null else 128
            else -> throw IllegalArgumentException(part)
        }
        val amp = DoubleArray(64)
        for (sc in 0 until 64) {
            val im = data[off + sc * 2].toInt().toDouble()
            val re = data[off + sc * 2 + 1].toInt().toDouble()
            amp[sc] = sqrt(im * im + re * re)
        }
        return DoubleArray(64) { amp[REORDER[it]] }
    }

    private fun searchLeft(t: DoubleArray, x: Double): Int {
        var lo = 0; var hi = t.size
        while (lo < hi) { val m = (lo + hi) ushr 1; if (t[m] < x) lo = m + 1 else hi = m }
        return lo
    }

    private fun colMean(a: Array<DoubleArray>, j: Int): Double { var s = 0.0; for (r in a) s += r[j]; return s / a.size }
    private fun colStd(a: Array<DoubleArray>, j: Int): Double {
        val m = colMean(a, j); var s = 0.0; for (r in a) { val d = r[j] - m; s += d * d }; return sqrt(s / a.size)
    }

    /** Motion statistics (e, e_last, jit, dev) of one link in [t0, t1); null if too few frames or live subcarriers. */
    fun linkStats(t: DoubleArray, A: Array<DoubleArray>, t0: Double, t1: Double): DoubleArray? {
        val i0 = searchLeft(t, t0); val i1 = searchLeft(t, t1)
        if (i1 - i0 < MIN_FRAMES) return null
        val tt = t.copyOfRange(i0, i1)
        var a = Array(i1 - i0) { A[i0 + it].copyOf() }
        val liveIdx = (0 until 64).filter { colMean(a, it) > NULL_AMP }
        if (liveIdx.size < MIN_LIVE_SC) return null
        a = Array(a.size) { r -> DoubleArray(liveIdx.size) { a[r][liveIdx[it]] } }
        // normalise each frame by its mean over live subcarriers (AGC / transmit power out)
        for (r in a.indices) { var m = 0.0; for (v in a[r]) m += v; m = m / a[r].size + 1e-3; for (c in a[r].indices) a[r][c] /= m }
        val sc = a[0].size
        var e = 0.0; for (j in 0 until sc) e += colStd(a, j); e /= sc
        val lastRows = a.indices.filter { tt[it] >= t1 - 1.0 }
        val eLast = if (lastRows.size >= 3) {
            val la = Array(lastRows.size) { a[lastRows[it]] }
            var s = 0.0; for (j in 0 until sc) s += colStd(la, j); s / sc
        } else e
        val nb = Math.round((t1 - t0) / BIN_S).toInt()
        val mean = DoubleArray(sc) { colMean(a, it) }
        val jits = ArrayList<Double>(); val devs = ArrayList<Double>()
        for (k in 0 until nb) {
            val rows = a.indices.filter { minOf(((tt[it] - t0) / BIN_S).toInt(), nb - 1) == k }
            if (rows.isEmpty()) continue
            val bin = Array(rows.size) { a[rows[it]] }
            var dv = 0.0; for (j in 0 until sc) dv += abs(colMean(bin, j) - mean[j]); devs.add(dv / sc)
            if (rows.size >= 2) { var jv = 0.0; for (j in 0 until sc) jv += colStd(bin, j); jits.add(jv / sc) }
        }
        val jit = if (jits.isNotEmpty()) median(jits) else e
        val dev = if (devs.isNotEmpty()) devs.sum() / devs.size else 0.0
        return doubleArrayOf(e, eLast, jit, dev)
    }

    private fun median(xs: List<Double>): Double {
        val s = xs.sorted(); val n = s.size
        return if (n % 2 == 1) s[n / 2] else (s[n / 2 - 1] + s[n / 2]) / 2.0
    }
}
