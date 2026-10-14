package ru.wsn.capture.core

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files

class LocalCaptureTest {
    private var hubT = 1_792_000_000.0
    private var boards = listOf("7c:4f:ad:bc:9b:54" to "Плата 1")
    private val opened = ArrayList<File?>()
    private val dir: File = Files.createTempDirectory("wsn-sessions").toFile()
    private val lc = LocalCapture(dir, { hubT }, { boards }, { opened.add(it) })

    private fun send(o: JSONObject) = lc.onMessage("phone", o.toString()).map { JSONObject(it) }
    private fun ev(seq: Int, kind: String, tMs: Double, extra: JSONObject = JSONObject()) =
        send(JSONObject(extra.toString()).put("type", "event").put("seq", seq).put("t", tMs).put("kind", kind))

    /** Phone clock 5000 s behind the hub; a symmetric 20 ms round trip. */
    private fun sync() {
        val pong = send(JSONObject().put("type", "ping").put("c0", 1000.0)).single()
        assertEquals("pong", pong.getString("type"))
        send(JSONObject().put("type", "sync").put("c0", 1000.0).put("s", 5001.010 + 0.0).put("c1", 1020.0))
    }

    @Test fun walkIsWrittenOnTheHubClock() {
        val hello = send(JSONObject().put("type", "hello").put("client_id", "phone"))
        assertEquals("state", hello[0].getString("type"))
        assertEquals("7c:4f:ad:bc:9b:54", hello[0].getJSONArray("devices").getJSONObject(0).getString("mac"))
        assertEquals("no_clock_sync", ev(1, "session_start", 2000.0)[0].getString("code"))
        sync()
        hubT = 1_792_000_100.0
        val r = ev(1, "session_start", 2000.0, JSONObject().put("session_kind", "walk"))
        assertEquals("ack", r[0].getString("type"))
        assertTrue(r[1].getBoolean("owner"))
        val sdir = opened.single()!!
        val poses = JSONArray().put(JSONObject().put("seq", 1).put("t", 3000.0).put("p", JSONArray(listOf(1.0, 0.0, -2.0))))
            .put(JSONObject().put("seq", 2).put("t", 3200.0).put("p", JSONArray(listOf(1.1, 0.0, -2.0))))
        assertEquals(2, send(JSONObject().put("type", "poses").put("items", poses)).single().getInt("pose_seq"))
        send(JSONObject().put("type", "poses").put("items", poses))  // повтор после переподключения: не дублируется
        ev(2, "step", 3500.0, JSONObject().put("activity", "empty").put("name", "empty1"))
        ev(2, "step", 3500.0, JSONObject().put("activity", "empty"))  // тот же seq: пропуск
        ev(3, "session_stop", 9000.0)
        assertNull(opened.last())
        val lines = File(sdir, "pose.jsonl").readLines()
        assertEquals(2, lines.size)
        assertEquals(5003.0, JSONObject(lines[0]).getDouble("t"), 1e-6)   // 3000 ms + offset 5000 s
        val events = File(sdir, "events.jsonl").readLines().map { JSONObject(it) }
        assertEquals(listOf("session_start", "step", "session_stop"), events.map { it.getString("kind") })
        val meta = JSONObject(File(sdir, "meta.json").readText())
        assertEquals(5009.0, meta.getDouble("t1"), 1e-6)
        assertNotNull(meta.optString("id"))
    }

    @Test fun noBoardNoSession() {
        boards = emptyList()
        sync()
        assertEquals("no_device", ev(1, "session_start", 2000.0)[0].getString("code"))
        assertTrue(opened.isEmpty())
    }
}
