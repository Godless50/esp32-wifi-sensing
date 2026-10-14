package ru.wsn.capture.core

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** CSI amplitude decode and window statistics must match the server; FrameFeed clock/selection logic is checked
 *  structurally. Golden in resources/model/csi_golden.json comes from live.ltf_amplitude and zonemodel.link_stats. */
class CsiTest {
    private fun g() = JSONObject(javaClass.getResourceAsStream("/model/csi_golden.json")!!.bufferedReader().readText())
    private fun vec(a: JSONArray) = DoubleArray(a.length()) { a.getDouble(it) }

    @Test fun amplitudeMatchesPython() {
        val cases = g().getJSONArray("amp")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val bytesJson = c.getJSONArray("bytes")
            val bytes = ByteArray(bytesJson.length()) { bytesJson.getInt(it).toByte() }
            val mine = Csi.ltfAmplitude(bytes, c.getString("part"))
            if (c.isNull("out")) { assertNull(mine); continue }
            val exp = vec(c.getJSONArray("out"))
            for (k in exp.indices) assertEquals("amp[$i][$k]", exp[k], mine!![k], 1e-4)
        }
    }

    @Test fun linkStatsMatchPython() {
        val cases = g().getJSONArray("stats")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val t = vec(c.getJSONArray("t"))
            val aj = c.getJSONArray("A")
            val a = Array(aj.length()) { vec(aj.getJSONArray(it)) }
            val mine = Csi.linkStats(t, a, c.getDouble("t0"), c.getDouble("t1"))
            if (c.isNull("out")) { assertNull("stats[$i] should be null", mine); continue }
            val exp = vec(c.getJSONArray("out"))
            for (k in exp.indices) assertEquals("stats[$i][$k]", exp[k], mine!![k], 1e-3)
        }
    }

    private fun frame(tUs: Long, src: String, len: Int) =
        Wsn1.CsiFrame(tUs, -45, -92, if (len >= 256) 1 else 0, 0, ByteArray(len) { (it % 7).toByte() }, src)

    private fun pkt(boot: Long, frames: List<Wsn1.CsiFrame>) =
        Wsn1.Packet(Wsn1.TYPE_CSI_SRC, "7c:4f:ad:bc:9b:54", boot, 0, frames)

    @Test fun frameFeedClockUsesMinimumAndPrefersHt() {
        val ff = FrameFeed()
        // first packet establishes the offset; a later packet that "arrived late" must not shift placement
        val p1 = pkt(1, listOf(frame(1_000_000, "ap", 256)))
        val f1 = ff.frames(p1, recvT = 10.0)
        assertEquals(1, f1.size); assertEquals("ht", f1[0].kind)
        assertEquals(9.0, ff.offset(p1, 10.0)!!, 1e-9)  // offset ~ recv - lastUs/1e6

        // an L-LTF-only frame from a new transmitter is used (no prior HT for it)
        val f2 = ff.frames(pkt(1, listOf(frame(2_000_000, "legacy", 128))), recvT = 11.0)
        assertEquals("lltf", f2.single().kind)

        // once a transmitter has an HT frame, a stray short frame within 10 s is skipped
        ff.frames(pkt(1, listOf(frame(3_000_000, "dev", 256))), recvT = 12.0)
        val f3 = ff.frames(pkt(1, listOf(frame(3_100_000, "dev", 128))), recvT = 12.1)
        assertTrue("stray L-LTF skipped while HT is fresh", f3.none { it.src == "dev" })
    }
}
