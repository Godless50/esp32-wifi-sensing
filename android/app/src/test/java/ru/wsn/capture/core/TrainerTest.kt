package ru.wsn.capture.core

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test

/** The on-phone trainer's deterministic core (forward, gradients, AdamW, partial-label loss) must match the
 *  Python reference in server (fit_net's math, run with a fixed init and no shuffle/dropout/noise). */
class TrainerTest {
    private fun g() = JSONObject(javaClass.getResourceAsStream("/model/train_golden.json")!!.bufferedReader().readText())
    private fun mat(a: JSONArray) = Array(a.length()) { i -> a.getJSONArray(i).let { r -> DoubleArray(r.length()) { r.getDouble(it) } } }
    private fun vec(a: JSONArray) = DoubleArray(a.length()) { a.getDouble(it) }
    private fun net(o: JSONObject) = Trainer.Net(mat(o.getJSONArray("W1")), vec(o.getJSONArray("b1")),
        mat(o.getJSONArray("W2")), vec(o.getJSONArray("b2")), mat(o.getJSONArray("Wz")), vec(o.getJSONArray("bz")),
        mat(o.getJSONArray("Wm")), vec(o.getJSONArray("bm")))

    private class Deterministic : Trainer.TrainRng {
        override fun shuffle(a: IntArray) {}                       // keep order, like the no-shuffle reference
        override fun dropout(rows: Int, links: Int, p: Double) = Array(rows) { BooleanArray(links) }
        override fun noise(rows: Int, cols: Int, sd: Double) = Array(rows) { DoubleArray(cols) }
    }

    private fun load3(a: JSONArray) = Array(a.length()) { n -> mat(a.getJSONArray(n)) }

    @Test fun standardisationMatchesPython() {
        val j = g()
        val x = load3(j.getJSONArray("X"))
        val (mu, sd) = Trainer.standardisation(x)
        val muE = mat(j.getJSONArray("mu")); val sdE = mat(j.getJSONArray("sd"))
        for (i in muE.indices) for (k in muE[i].indices) {
            assertEquals("mu[$i][$k]", muE[i][k], mu[i][k], 1e-9)
            assertEquals("sd[$i][$k]", sdE[i][k], sd[i][k], 1e-9)
        }
    }

    @Test fun sampleWeightsMatchPython() {
        val j = g()
        val z = mat(j.getJSONArray("Z"))
        val w = Trainer.sampleWeights(z); val wE = vec(j.getJSONArray("sample_w"))
        for (i in wE.indices) assertEquals("w[$i]", wE[i], w[i], 1e-9)
    }

    @Test fun trainedWeightsMatchPython() {
        val j = g()
        val x = load3(j.getJSONArray("X"))
        val z = mat(j.getJSONArray("Z")); val m = vec(j.getJSONArray("M"))
        val mu = mat(j.getJSONArray("mu")); val sd = mat(j.getJSONArray("sd"))
        val cfg = j.getJSONObject("cfg")
        val config = Trainer.FitConfig(hidden = j.getInt("h1") to j.getInt("h2"), epochs = cfg.getInt("epochs"),
            batch = cfg.getInt("batch"), lr = cfg.getDouble("lr"), weightDecay = cfg.getDouble("weight_decay"),
            linkDrop = 0.0, noise = 0.0, moveW = cfg.getDouble("move_w"))
        val out = Trainer.fitNet(x, z, m, mu, sd, net(j.getJSONObject("init")), config, Deterministic())
        val exp = net(j.getJSONObject("final"))
        fun cmpM(name: String, a: Array<DoubleArray>, b: Array<DoubleArray>) {
            for (i in a.indices) for (k in a[i].indices) assertEquals("$name[$i][$k]", b[i][k], a[i][k], 1e-6)
        }
        fun cmpV(name: String, a: DoubleArray, b: DoubleArray) { for (i in a.indices) assertEquals("$name[$i]", b[i], a[i], 1e-6) }
        cmpM("W1", out.w1, exp.w1); cmpV("b1", out.b1, exp.b1)
        cmpM("W2", out.w2, exp.w2); cmpV("b2", out.b2, exp.b2)
        cmpM("Wz", out.wz, exp.wz); cmpV("bz", out.bz, exp.bz)
        cmpM("Wm", out.wm, exp.wm); cmpV("bm", out.bm, exp.bm)
    }
}
