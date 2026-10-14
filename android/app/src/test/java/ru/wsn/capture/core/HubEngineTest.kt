package ru.wsn.capture.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** The whole phone-hub path on the JVM: signed CSI datagrams -> per-link buffers -> 4 s window -> verdict.
 *  Uses a tiny hand-built model (links as device|src MAC pairs) and crafted CSI_SRC packets. */
class HubEngineTest {
    private val key = ByteArray(32) { (it + 1).toByte() }
    private val dev = "7c:4f:ad:bc:9b:54"
    private val ap = "98:b3:ef:02:da:28"
    private val d1 = "d8:d6:68:b3:4c:23"

    /** Minimal 2-link model: zones [зал, пусто]; weights make "зал" win so verdict is deterministic-ish. */
    private fun tinyModel(): ZoneModel {
        val l = 2; val nf = ZoneFeatures.NF; val d = l * nf
        fun zeros(r: Int, c: Int) = (0 until r).joinToString(",", "[", "]") { (0 until c).joinToString(",", "[", "]") { "0.0" } }
        // W1 all zeros, bz = [1,0] so softmax favours room 0 regardless of input; moving bias 0.
        val w1 = zeros(d, 4); val w2 = zeros(4, 4); val wz = zeros(4, 2); val wm = zeros(4, 1)
        val mu = (0 until l).joinToString(",", "[", "]") { (0 until nf).joinToString(",", "[", "]") { "0.0" } }
        val sd = (0 until l).joinToString(",", "[", "]") { (0 until nf).joinToString(",", "[", "]") { "1.0" } }
        val net = """{"W1":$w1,"b1":[0,0,0,0],"W2":$w2,"b2":[0,0,0,0],"Wz":$wz,"bz":[1.0,0.0],"Wm":$wm,"bm":[0.0]}"""
        val json = """{"version":1,"links":["$dev|$ap","$dev|$d1"],"zones":["зал","пусто"],"mu":$mu,"sd":$sd,"nets":[$net]}"""
        return ZoneModel.fromJson(json)
    }

    /** A signed WSN1 CSI_SRC datagram with `n` HT frames (256-byte LTF) per given src, amplitudes near `level`. */
    private fun csiSrc(boot: Long, seq: Long, tUs: Long, srcs: List<String>, jitter: Int): ByteArray {
        val body = ByteArrayOutputStream()
        val hdr = ByteBuffer.allocate(21).order(ByteOrder.LITTLE_ENDIAN)
        hdr.put("WSN1".toByteArray()); hdr.put(1); hdr.put(Wsn1.TYPE_CSI_SRC.toByte())
        hdr.put(Wsn1.macBytes(dev)); hdr.putInt(boot.toInt()); hdr.putInt(seq.toInt()); hdr.put(srcs.size.toByte())
        body.write(hdr.array())
        for (src in srcs) {
            val fh = ByteBuffer.allocate(20).order(ByteOrder.LITTLE_ENDIAN)
            fh.putLong(tUs); fh.put((-45).toByte()); fh.put((-92).toByte()); fh.put(1); fh.put(0); fh.putShort(256)
            fh.put(Wsn1.macBytes(src))
            body.write(fh.array())
            val data = ByteArray(256)
            for (sc in 0 until 64) {  // HT-LTF is the second 128 bytes; give most subcarriers |iq| ~ 12 (live)
                data[128 + sc * 2] = (8 + (sc + jitter) % 5).toByte()      // imag
                data[128 + sc * 2 + 1] = (9 + (sc * 2 + jitter) % 7).toByte() // real
            }
            body.write(data)
        }
        val b = body.toByteArray()
        return b + Wsn1.hmac16(key, b)
    }

    @Test fun datagramStreamProducesVerdict() {
        val engine = HubEngine(model = tinyModel(), keyFor = { if (it == dev) key else null })
        var seq = 1L
        // ~40 frames over 4 s for both links; vary jitter so std > 0 (motion)
        for (f in 0 until 40) {
            val t = f * 100_000L  // 10 Hz, tUs
            engine.onDatagram(csiSrc(1, seq++, t, listOf(ap, d1), f), recvT = 1000.0 + f * 0.1)
        }
        val now = 1000.0 + 39 * 0.1 + HubEngine.LAG_S
        val v = engine.tick(now)
        assertNotNull(v)
        assertEquals("зал", v!!.zone)                       // bz favours room 0
        assertEquals(2, v.linksLive)                        // both links had frames in the window
        assertEquals(1.0, v.probs.values.sum(), 1e-6)
        assertTrue(engine.liveLinks(now).contains("$dev|$ap"))
    }

    @Test fun rejectsBadSignatureAndNoModel() {
        var raw = 0
        val engine = HubEngine(keyFor = { key }, rawSink = { _, _ -> raw++ })
        val good = csiSrc(1, 1, 0, listOf(ap), 0)
        assertNotNull(engine.onDatagram(good, 1.0))
        val bad = good.copyOf(); bad[bad.size - 1] = (bad[bad.size - 1] + 1).toByte()
        assertNull(engine.onDatagram(bad, 1.1))             // HMAC mismatch dropped
        assertEquals(1, raw)                                // only the good CSI datagram was recorded
        assertNull(engine.tick(5.0))                        // no model loaded
    }
}
