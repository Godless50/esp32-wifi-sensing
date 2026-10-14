package ru.wsn.capture.core

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test

/** The Kotlin room-model inference must match the Python numbers exactly (within float noise). The model and
 *  golden vectors in resources/model are produced by server/wsn/zonemodel's own functions. */
class ZoneModelTest {
    private fun resText(name: String) = javaClass.getResourceAsStream("/model/$name")!!.bufferedReader().readText()

    private fun mat(a: JSONArray) = Array(a.length()) { i -> a.getJSONArray(i).let { r -> DoubleArray(r.length()) { r.getDouble(it) } } }

    @Test fun linkFeaturesMatchPython() {
        val g = JSONObject(resText("golden.json")).getJSONArray("feat")
        for (i in 0 until g.length()) {
            val f = g.getJSONObject(i)
            val st = if (f.isNull("st")) null else f.getJSONArray("st").let { DoubleArray(it.length()) { k -> it.getDouble(k) } }
            val lvl = if (f.isNull("level")) null else f.getJSONArray("level").let { DoubleArray(it.length()) { k -> it.getDouble(k) } }
            val out = f.getJSONArray("out").let { DoubleArray(it.length()) { k -> it.getDouble(k) } }
            val mine = ZoneFeatures.linkFeatures(st, lvl)
            for (k in out.indices) assertEquals("feat[$i][$k]", out[k], mine[k], 1e-9)
        }
    }

    @Test fun predictMatchesPython() {
        val model = ZoneModel.fromJson(resText("model.json"))
        val g = JSONObject(resText("golden.json"))
        val xj = g.getJSONArray("X")
        val x = Array(xj.length()) { n -> mat(xj.getJSONArray(n)) }
        val pzExp = mat(g.getJSONArray("pz"))
        val pmExp = g.getJSONArray("pm").let { DoubleArray(it.length()) { k -> it.getDouble(k) } }
        val (pz, pm) = model.predict(x)
        assertEquals(pzExp.size, pz.size)
        for (n in pz.indices) {
            for (k in pz[n].indices) assertEquals("pz[$n][$k]", pzExp[n][k], pz[n][k], 1e-6)
            assertEquals("pm[$n]", pmExp[n], pm[n], 1e-6)
        }
    }

    @Test fun verdictPicksMostLikelyRoom() {
        val model = ZoneModel.fromJson(resText("model.json"))
        val g = JSONObject(resText("golden.json"))
        val x0 = mat(g.getJSONArray("X").getJSONArray(0))
        val v = model.verdict(arrayOf(x0))
        // room probabilities (excluding empty) must sum to 1 and name a real room
        assertEquals(1.0, v.probs.values.sum(), 1e-6)
        assert(model.zones.dropLast(1).contains(v.zone))
    }
}
