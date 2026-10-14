package ru.wsn.capture.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** The server half of the capture protocol (server/wsn/capture.py) running inside the phone that is the hub:
 *  the AR walk talks to it exactly as it talks to the computer server, but sessions are written to the phone's
 *  own storage, in the hub's clock (the same seconds the raw datagrams are stamped with), so training can put the
 *  phone's positions and the boards' CSI on one time axis without any network.
 *
 *  Session layout (one folder per recording): meta.json {id, kind, t0, t1}, events.jsonl, pose.jsonl, and the raw
 *  datagrams the hub records while the session is open (csi.wsn, written by the hub service via [onSession]).
 *  Not thread-safe: call from one thread. */
class LocalCapture(
    private val sessionsDir: File,
    private val clock: () -> Double,                       // seconds: the hub's clock
    private val boards: () -> List<Pair<String, String>>,  // boards on the air: mac, name
    private val onSession: (File?) -> Unit = {},           // a session opened (its folder) / closed (null)
) {
    private class Client {
        var poseSeq = 0
        var eventSeq = 0
        var offset: Double? = null   // hub seconds - phone seconds
        var bestRtt = Double.MAX_VALUE
    }

    private val clients = HashMap<String, Client>()
    private var owner: String? = null
    var active: JSONObject? = null
        private set
    var activeDir: File? = null
        private set

    private fun err(code: String, message: String = code) =
        JSONObject().put("type", "error").put("code", code).put("message", message)

    private fun ack(c: Client) = JSONObject().put("type", "ack").put("pose_seq", c.poseSeq).put("event_seq", c.eventSeq)

    private fun state(cid: String): JSONObject {
        val c = clients.getOrPut(cid) { Client() }
        val devs = JSONArray()
        for ((mac, name) in boards()) devs.put(JSONObject().put("mac", mac).put("role", "board").put("name", name))
        return JSONObject().put("type", "state").put("session", active ?: JSONObject.NULL)
            .put("owner", active != null && owner == cid).put("clock_synced", c.offset != null).put("devices", devs)
    }

    /** One message from the phone UI (cid = its client id); returns the replies, in order. */
    fun onMessage(cid: String, text: String): List<String> {
        val m = try { JSONObject(text) } catch (e: Exception) { return listOf(err("bad_json").toString()) }
        val c = clients.getOrPut(cid) { Client() }
        val out = when (m.optString("type")) {
            "hello" -> listOf(state(cid), ack(c))
            "ping" -> listOf(JSONObject().put("type", "pong").put("c0", m.opt("c0")).put("s", clock()))
            "sync" -> { sync(c, m); emptyList() }
            "poses" -> poses(cid, c, m.optJSONArray("items") ?: JSONArray())
            "event" -> event(cid, c, m)
            else -> listOf(err("bad_type", "unknown message type ${m.optString("type")}"))
        }
        return out.map { it.toString() }
    }

    /** c0, c1: phone ms around the round trip; s: hub seconds. The sample with the shortest trip wins. */
    private fun sync(c: Client, m: JSONObject) {
        val c0 = m.optDouble("c0", Double.NaN); val c1 = m.optDouble("c1", Double.NaN); val s = m.optDouble("s", Double.NaN)
        if (c0.isNaN() || c1.isNaN() || s.isNaN() || c1 < c0) return
        val rtt = c1 - c0
        if (rtt <= c.bestRtt) {
            c.bestRtt = rtt
            c.offset = s - (c0 + c1) / 2.0 / 1000.0
        }
    }

    private fun toHub(c: Client, phoneMs: Double) = phoneMs / 1000.0 + c.offset!!

    private fun owns(cid: String) = active != null && owner == cid

    private fun append(name: String, obj: JSONObject) {
        File(activeDir!!, name).appendText(obj.toString() + "\n")
    }

    private fun poses(cid: String, c: Client, items: JSONArray): List<JSONObject> {
        val fresh = (0 until items.length()).mapNotNull { items.optJSONObject(it) }.filter { it.optInt("seq") > c.poseSeq }
            .sortedBy { it.optInt("seq") }
        if (fresh.isEmpty()) return listOf(ack(c))
        if (active == null) { c.poseSeq = fresh.last().optInt("seq"); return listOf(err("no_session"), ack(c)) }
        if (!owns(cid)) return listOf(err("not_owner", "another phone is recording"))
        if (c.offset == null) return listOf(err("no_clock_sync"))
        val sb = StringBuilder()
        for (it in fresh) {
            sb.append(JSONObject().put("seq", it.optInt("seq")).put("t", toHub(c, it.optDouble("t")))
                .put("p", it.opt("p")).put("q", it.opt("q")).toString()).append('\n')
            c.poseSeq = it.optInt("seq")
        }
        File(activeDir!!, "pose.jsonl").appendText(sb.toString())
        return listOf(ack(c))
    }

    private fun event(cid: String, c: Client, m: JSONObject): List<JSONObject> {
        val seq = m.optInt("seq")
        val kind = m.optString("kind")
        if (kind !in EVENT_KINDS) return listOf(err("bad_event", "unknown event $kind"))
        if (seq <= c.eventSeq) return listOf(ack(c))
        if (c.offset == null) return listOf(err("no_clock_sync"))
        val rec = JSONObject(m.toString()).apply { remove("type"); put("t_phone", m.opt("t")); put("t", toHub(c, m.optDouble("t"))) }
        if (kind == "session_start") {
            if (active != null && !owns(cid)) return listOf(err("session_busy", "another session is recording"))
            if (active == null) {
                if (boards().isEmpty()) return listOf(err("no_device", "no board is streaming CSI"))
                start(m.optString("session_kind", "walk"), rec.getDouble("t"))
                owner = cid
            }
            append("events.jsonl", rec)
        } else if (!owns(cid)) {
            return listOf(err(if (active == null) "no_session" else "not_owner"))
        } else {
            append("events.jsonl", rec)
            if (kind == "session_stop") stop(rec.getDouble("t"))
        }
        c.eventSeq = seq
        return listOf(ack(c), state(cid))
    }

    private fun start(kind: String, t0: Double) {
        val id = SimpleDateFormat("yyyyMMdd-HHmmss", Locale.ROOT).format(Date((t0 * 1000).toLong()))
        val dir = File(sessionsDir, id).apply { mkdirs() }
        val meta = JSONObject().put("id", id).put("kind", kind).put("t0", t0)
        File(dir, "meta.json").writeText(meta.toString())
        active = meta; activeDir = dir
        onSession(dir)
    }

    private fun stop(t1: Double) {
        val dir = activeDir ?: return
        val meta = active!!.put("t1", t1)
        File(dir, "meta.json").writeText(meta.toString())
        active = null; activeDir = null; owner = null
        onSession(null)
    }

    /** Close an open session (the app is being stopped): it keeps what was recorded, with its end now. */
    fun abort() { if (active != null) stop(clock()) }

    companion object {
        val EVENT_KINDS = setOf("session_start", "session_stop", "origin", "router", "room_new", "corner", "room_close",
            "object", "label", "device", "step")
    }
}
