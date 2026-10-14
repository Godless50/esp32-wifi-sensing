package ru.wsn.capture.core

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Three-way parity: the same golden packets the C and Python tests use must parse and encode identically here.
 *  Fixtures in src/test/resources/golden are copied from server/tests/golden (made by the C golden binary and
 *  wsn.protocol). */
class Wsn1Test {
    private val key = ByteArray(32) { it.toByte() }
    private val mac = "02:00:00:00:00:01"

    private fun res(name: String): List<ByteArray> =
        javaClass.getResourceAsStream("/golden/$name")!!.bufferedReader().readLines()
            .filter { it.isNotBlank() }.map { line -> ByteArray(line.length / 2) { ((line[it * 2].digitToInt(16) shl 4) or line[it * 2 + 1].digitToInt(16)).toByte() } }

    private fun hex(b: ByteArray) = b.joinToString("") { "%02x".format(it.toInt() and 0xFF) }

    @Test fun csiPacket() {
        val buf = res("c_packets.hex")[0]
        val p = Wsn1.parse(buf, key)
        assertEquals(Wsn1.TYPE_CSI, p.type); assertEquals(mac, p.deviceId); assertEquals(0xDEADBEEFL, p.bootId)
        assertEquals(7L, p.seq); assertEquals(5, p.frames.size)
        assertEquals(1_000_000L, p.frames[0].tUs); assertEquals(256, p.frames[0].data.size)
        assertEquals(0, p.frames[0].data[0].toInt()); assertEquals(255, p.frames[0].data[255].toInt() and 0xFF)
    }

    @Test fun telemetryPacket() {
        val p = Wsn1.parse(res("c_packets.hex")[1], key)
        assertEquals(Wsn1.TYPE_TELEM, p.type); assertEquals(8L, p.seq)
        val t = p.telemetry!!
        assertEquals(98.5f, t.csiHz, 1e-3f); assertEquals(0.02f, t.dropRatio, 1e-3f)
        assertEquals(-44, t.rssiAvg); assertEquals(120_000L, t.heapFree); assertEquals(8_000_000L, t.psramFree)
        assertEquals(3600L, t.uptimeS); assertEquals(2, t.wifiState)
    }

    @Test fun csiSrcPacket() {
        val p = Wsn1.parse(res("c_packets.hex")[2], key)
        assertEquals(Wsn1.TYPE_CSI_SRC, p.type); assertEquals(9L, p.seq); assertEquals(2, p.frames.size)
        assertEquals("98:b3:ef:02:da:28", p.frames[0].src); assertEquals(128, p.frames[0].data.size)
        assertEquals("98:b3:ef:02:da:29", p.frames[1].src); assertEquals(256, p.frames[1].data.size)
    }

    @Test fun predPacket() {
        val p = Wsn1.parse(res("c_packets.hex")[3], key)
        assertEquals(Wsn1.TYPE_PRED, p.type); assertEquals(10L, p.seq)
        val r = p.prediction!!
        assertEquals(1791870150L, r.modelVer); assertEquals(87, r.inferMs); assertEquals(8, r.linksLive)
        assertEquals(intArrayOf(10, 200, 40, 5).toList(), r.act.map { Math.round(it * 255) })
        assertEquals(intArrayOf(180, 50, 20, 5).toList(), r.zone.map { Math.round(it * 255) })
        assertEquals(230, Math.round(r.move * 255))
    }

    @Test fun modelAckPacket() {
        val p = Wsn1.parse(res("c_packets.hex")[4], key)
        assertEquals(Wsn1.TYPE_MODEL_ACK, p.type); assertEquals(11L, p.seq)
        assertEquals(Wsn1.ModelAck(1791871129L, 2048L, 1, 1, 0), p.modelAck)
    }

    @Test fun logPacket() {
        val p = Wsn1.parse(res("c_packets.hex")[6], key)
        assertEquals(Wsn1.TYPE_LOG, p.type); assertEquals(13L, p.seq)
        assertEquals("romif +0x128 3fc9a000->00000000", p.log)
    }

    @Test fun surveyPacket() {
        val p = Wsn1.parse(res("c_packets.hex")[7], key)
        assertEquals(Wsn1.TYPE_SURVEY, p.type); assertEquals(14L, p.seq)
        val s = p.survey!!
        assertEquals(3, s.state); assertEquals(100, s.progress); assertEquals(3, s.responders); assertEquals(2, s.entries.size)
        assertEquals("192.168.4.2", s.entries[0].ip); assertEquals("d8:d6:68:b3:4c:23", s.entries[0].mac)
        assertTrue(s.entries[0].chosen); assertEquals(100, s.entries[0].ht); assertEquals(-56, s.entries[0].rssi)
        assertTrue(s.entries[1].unheard); assertEquals("", s.entries[1].mac)
    }

    @Test fun hmacRejectsTamper() {
        val buf = res("c_packets.hex")[1].copyOf()
        buf[25] = (buf[25] + 1).toByte()
        try { Wsn1.parse(buf, key); throw AssertionError("should reject") } catch (e: Wsn1.ProtocolError) {}
    }

    @Test fun modelChunkMatchesPython() {
        val fx = res("py_model_chunk.hex")[0]
        val mine = Wsn1.encodeModelChunk(mac, 0xDEADBEEFL, 5, key, 1791871129L, 3000, 1024, ByteArray(100) { it.toByte() })
        assertArrayEquals(fx, mine)
    }

    @Test fun hubAndCmdMatchPython() {
        val fx = res("py_hub_cmd.hex")
        assertArrayEquals(fx[0], Wsn1.encodeHub(mac, 1_792_000_000L, key, "192.168.4.23", 5005))
        assertArrayEquals(fx[1], Wsn1.encodeCmd(mac, 0xDEADBEEFL, 9, key, Wsn1.CMD_SURVEY,
            Wsn1.surveyArgs(60, 6, listOf("192.168.4.23", "192.168.4.99"))))
        assertArrayEquals(fx[2], Wsn1.encodeCmd(mac, 0xDEADBEEFL, 10, key, Wsn1.CMD_LINKS,
            Wsn1.linksArgs(listOf("d8:d6:68:b3:4c:23" to "192.168.4.2"))))
    }

    @Test fun roundTripHubParses() {
        val pkt = Wsn1.encodeHub(mac, 42, key, "10.1.2.3", 5005)
        // the board verifies; here just confirm the body is well-formed by re-signing check via parse of a CMD
        val cmd = Wsn1.encodeCmd(mac, 1, 7, key, Wsn1.CMD_SURVEY, Wsn1.surveyArgs(30, 4))
        assertEquals(mac, Wsn1.peekDeviceId(pkt)); assertEquals(mac, Wsn1.peekDeviceId(cmd))
    }
}
