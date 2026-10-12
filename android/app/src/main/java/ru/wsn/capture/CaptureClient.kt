package ru.wsn.capture

import org.json.JSONArray
import org.json.JSONObject

/**
 * Phone side of the /ws/capture protocol (same as ui/capture/capture.js), without Android APIs.
 * Not thread-safe: call from one thread (the app uses the main thread via a handler).
 *
 * Times are phone milliseconds from [now]. Unacknowledged poses/events stay queued and are resent;
 * the server drops duplicates by seq. After (re)connect, the server's ack continues our numbering.
 */
class CaptureClient(
    private val clientId: String,
    private val now: () -> Double,
    private val onError: (code: String, message: String) -> Unit = { _, _ -> },
) {
    fun interface Sink { fun send(text: String) }

    var sink: Sink? = null
    var online = false
        private set
    var clockSynced = false
        private set
    var csiHz = 0.0
        private set
    var session: JSONObject? = null
        private set
    var owner = false
        private set

    private var poseSeq = 0
    private var eventSeq = 0
    private var ackPose = 0
    private var ackEvent = 0
    private val pendingPoses = ArrayDeque<JSONObject>()
    private val pendingEvents = ArrayDeque<JSONObject>()

    fun pendingCount(): Int = pendingPoses.size + pendingEvents.size

    fun recording(): Boolean = owner && session?.optString("kind") == "walk"

    fun onOpen() {
        online = true
        send(JSONObject().put("type", "hello").put("client_id", clientId))
        ping()
    }

    fun onClosed() {
        online = false
        clockSynced = false
    }

    fun ping() = send(JSONObject().put("type", "ping").put("c0", now()))

    fun onMessage(text: String) {
        val m = try { JSONObject(text) } catch (e: Exception) { return }
        when (m.optString("type")) {
            "pong" -> send(JSONObject().put("type", "sync").put("c0", m.getDouble("c0"))
                .put("s", m.getDouble("s")).put("c1", now()))
            "ack" -> {
                ackPose = maxOf(ackPose, m.optInt("pose_seq"))
                ackEvent = maxOf(ackEvent, m.optInt("event_seq"))
                poseSeq = maxOf(poseSeq, ackPose)
                eventSeq = maxOf(eventSeq, ackEvent)
                while (pendingPoses.isNotEmpty() && pendingPoses.first().getInt("seq") <= ackPose) pendingPoses.removeFirst()
                pendingEvents.removeAll { it.getInt("seq") <= ackEvent }
            }
            "state" -> {
                session = m.optJSONObject("session")
                owner = m.optBoolean("owner")
                clockSynced = m.optBoolean("clock_synced")
                if (m.has("csi_hz")) csiHz = m.optDouble("csi_hz", 0.0)
            }
            "error" -> onError(m.optString("code"), m.optString("message"))
        }
    }

    /** Queue a pose (only while this phone records a walk session). */
    fun addPose(t: Double, p: FloatArray, q: FloatArray) {
        if (!recording()) return
        val item = JSONObject().put("seq", ++poseSeq).put("t", t)
            .put("p", JSONArray(p.map { it.toDouble() })).put("q", JSONArray(q.map { it.toDouble() }))
        pendingPoses.addLast(item)
        while (pendingPoses.size > MAX_PENDING) pendingPoses.removeFirst()
    }

    fun flushPoses() {
        if (!online || pendingPoses.isEmpty()) return
        val items = JSONArray()
        for (p in pendingPoses) {
            if (p.getInt("seq") <= ackPose) continue
            items.put(p)
            if (items.length() >= MAX_BATCH) break
        }
        if (items.length() > 0) send(JSONObject().put("type", "poses").put("items", items))
    }

    /** Create, queue and (if online) send an event. Returns the event for logging. */
    fun event(kind: String, extra: JSONObject = JSONObject()): JSONObject {
        val e = JSONObject(extra.toString()).put("type", "event").put("seq", ++eventSeq).put("t", now()).put("kind", kind)
        pendingEvents.addLast(e)
        if (online) send(e)
        return e
    }

    fun resendEvents() {
        if (!online) return
        for (e in pendingEvents) send(e)
    }

    private fun send(obj: JSONObject) {
        if (online) sink?.send(obj.toString())
    }

    companion object {
        const val MAX_BATCH = 300
        const val MAX_PENDING = 30 * 60 * 10  // 10 минут при 30 Гц
    }
}
