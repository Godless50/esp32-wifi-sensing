package ru.wsn.capture

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class CaptureClientTest {
    private val sent = mutableListOf<JSONObject>()
    private val errors = mutableListOf<String>()
    private var clockMs = 1000.0
    private lateinit var c: CaptureClient

    @Before
    fun setUp() {
        c = CaptureClient("phone-test", now = { clockMs }, onError = { code, _ -> errors.add(code) })
        c.sink = CaptureClient.Sink { sent.add(JSONObject(it)) }
    }

    private fun server(json: String) = c.onMessage(json)
    private fun sentOfType(t: String) = sent.filter { it.getString("type") == t }
    private fun startWalk() {
        c.onOpen()
        server("""{"type":"state","session":{"id":"s1","kind":"walk","started_wall":1.0},"owner":true,"clock_synced":true}""")
    }

    @Test
    fun openSendsHelloAndPing() {
        c.onOpen()
        assertEquals("phone-test", sentOfType("hello").single().getString("client_id"))
        assertEquals(1000.0, sentOfType("ping").single().getDouble("c0"), 0.0)
    }

    @Test
    fun pongAnsweredWithSyncCarryingC1() {
        c.onOpen()
        clockMs = 1012.0
        server("""{"type":"pong","c0":1000.0,"s":55.5}""")
        val s = sentOfType("sync").single()
        assertEquals(1000.0, s.getDouble("c0"), 0.0)
        assertEquals(55.5, s.getDouble("s"), 0.0)
        assertEquals(1012.0, s.getDouble("c1"), 0.0)
    }

    @Test
    fun ackFromServerContinuesNumberingAfterReinstall() {
        c.onOpen()
        server("""{"type":"ack","pose_seq":500,"event_seq":7}""")
        val e = c.event("origin", JSONObject().put("p", listOf(0, 1.4, 0)))
        assertEquals(8, e.getInt("seq"))
        startWalk()
        c.addPose(2000.0, floatArrayOf(0f, 1f, 0f), floatArrayOf(0f, 0f, 0f, 1f))
        c.flushPoses()
        assertEquals(501, sentOfType("poses").last().getJSONArray("items").getJSONObject(0).getInt("seq"))
    }

    @Test
    fun posesIgnoredWhenNotRecording() {
        c.onOpen()
        c.addPose(2000.0, floatArrayOf(0f, 1f, 0f), floatArrayOf(0f, 0f, 0f, 1f))
        assertEquals(0, c.pendingCount())
    }

    @Test
    fun batchesOf300AndAckDropsConfirmed() {
        startWalk()
        repeat(500) { c.addPose(2000.0 + it, floatArrayOf(it.toFloat(), 1f, 0f), floatArrayOf(0f, 0f, 0f, 1f)) }
        c.flushPoses()
        val batch = sentOfType("poses").last().getJSONArray("items")
        assertEquals(300, batch.length())
        assertEquals(1, batch.getJSONObject(0).getInt("seq"))
        server("""{"type":"ack","pose_seq":300,"event_seq":0}""")
        assertEquals(200, c.pendingCount())
        c.flushPoses()
        assertEquals(301, sentOfType("poses").last().getJSONArray("items").getJSONObject(0).getInt("seq"))
    }

    @Test
    fun eventsQueuedOfflineAreResentAfterReconnect() {
        val e = c.event("session_start", JSONObject().put("session_kind", "walk"))
        assertTrue(sentOfType("event").isEmpty())  // нет связи — только в очереди
        c.onOpen()
        c.resendEvents()
        assertEquals(e.getInt("seq"), sentOfType("event").single().getInt("seq"))
        server("""{"type":"ack","pose_seq":0,"event_seq":1}""")
        sent.clear()
        c.resendEvents()
        assertTrue(sentOfType("event").isEmpty())
    }

    @Test
    fun closeMarksOfflineAndUnsynced() {
        startWalk()
        c.onClosed()
        assertEquals(false, c.online)
        assertEquals(false, c.clockSynced)
    }

    @Test
    fun serverErrorsReported() {
        c.onOpen()
        server("""{"type":"error","code":"bad_polygon","message":"too few corners"}""")
        assertEquals(listOf("bad_polygon"), errors)
    }

    @Test
    fun stateExposesRecordingFlags() {
        startWalk()
        assertTrue(c.recording())
        server("""{"type":"state","session":null,"owner":false,"clock_synced":true,"csi_hz":97.0}""")
        assertEquals(false, c.recording())
        assertEquals(97.0, c.csiHz, 0.0)
    }
}
