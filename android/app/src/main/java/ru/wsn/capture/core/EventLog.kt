package ru.wsn.capture.core

import org.json.JSONArray
import org.json.JSONObject

/** The hub's diary: changes of the home state, not every verdict. A new state is written only after it has held
 *  for a while, so a one-second flicker never becomes an event; «никого дома» must hold longer than the rest (a person
 *  sitting still can look empty for a few seconds). Each event carries the time the new state began and the state
 *  before it, so the day can be drawn (HomeDay) and told («пришли», «снова на связи»).
 *  NODATA: the phone is not on the home Wi-Fi, so nothing is known — that is not «жучок молчит». */
class EventLog(private val holdS: Double = 10.0, private val emptyHoldS: Double = 60.0, private val keep: Int = 400) {
    enum class State { EMPTY, QUIET, MOVING, OFFLINE, NODATA }
    data class Event(val t: Double, val state: State, val zone: String?, val before: State?)

    private var shown: Pair<State, String?>? = null
    private var cand: Pair<State, String?>? = null
    private var candT = 0.0
    private val list = ArrayDeque<Event>()

    @Synchronized fun events(): List<Event> = list.toList()   // newest first

    /** Feed the state once per tick; returns the event when one is written. The zone counts only while somebody is home. */
    @Synchronized fun feed(t: Double, state: State, zone: String? = null): Event? {
        val present = state == State.QUIET || state == State.MOVING
        val s = state to (if (present) zone else null)
        if (s == shown) { cand = null; return null }
        if (s != cand) { cand = s; candT = t; return null }
        if (t - candT < if (state == State.EMPTY) emptyHoldS else holdS) return null
        val e = Event(candT, s.first, s.second, shown?.first)
        shown = s; cand = null
        list.addFirst(e)
        while (list.size > keep) list.removeLast()
        return e
    }

    @Synchronized fun toJson(): String = JSONArray().apply {
        for (e in list) put(JSONObject().put("t", e.t).put("s", e.state.name).put("z", e.zone ?: JSONObject.NULL)
            .put("b", e.before?.name ?: JSONObject.NULL))
    }.toString()

    /** Restore after a restart; the last written state counts as shown, so it is not written twice.
     *  Entries of the older diary format («PRESENT») read as QUIET. */
    @Synchronized fun load(json: String) {
        list.clear()
        val a = try { JSONArray(json) } catch (e: Exception) { return }
        fun st(s: String) = if (s == "PRESENT") State.QUIET else State.valueOf(s)
        for (i in 0 until minOf(a.length(), keep)) try {
            val o = a.getJSONObject(i)
            list.addLast(Event(o.getDouble("t"), st(o.getString("s")), o.optString("z").takeIf { !o.isNull("z") && it.isNotEmpty() },
                if (o.isNull("b")) null else st(o.getString("b"))))
        } catch (_: Exception) {}
        shown = list.firstOrNull()?.let { it.state to it.zone }
    }
}

/** One day of the diary for the main screen: coloured segments of the day line (hours 0..24) and the event list,
 *  in the words of the screen («Двигались 17:30–17:42», «Кто-то пришёл», «Жучок молчал 02:10–02:40»…). */
object HomeDay {
    data class Seg(val from: Double, val to: Double, val state: String)
    data class Item(val kind: String, val t: Double, val t2: Double?, val zone: String?)

    private fun name(s: EventLog.State) = when (s) {
        EventLog.State.EMPTY -> "empty"; EventLog.State.QUIET -> "quiet"; EventLog.State.MOVING -> "moving"
        EventLog.State.OFFLINE -> "offline"; EventLog.State.NODATA -> "nodata"
    }

    /** events: newest first (as EventLog gives them); dayStart/now in unix seconds. */
    fun build(events: List<EventLog.Event>, dayStart: Double, now: Double): Pair<List<Seg>, List<Item>> {
        val chron = events.sortedBy { it.t }
        val before = chron.lastOrNull { it.t < dayStart }
        val today = chron.filter { it.t in dayStart..now }
        val h = { t: Double -> ((t - dayStart) / 3600.0).coerceIn(0.0, 24.0) }
        val segs = ArrayList<Seg>()
        var curState = before?.state
        var curT = dayStart
        for (e in today) {
            if (curState != null && e.t > curT) segs += Seg(h(curT), h(e.t), name(curState))
            curState = e.state; curT = e.t
        }
        if (curState != null && now > curT) segs += Seg(h(curT), h(now), name(curState))

        val items = ArrayList<Item>()
        for ((i, e) in today.withIndex()) {
            val end = today.getOrNull(i + 1)?.t
            val present = e.state == EventLog.State.QUIET || e.state == EventLog.State.MOVING
            if (e.before == EventLog.State.OFFLINE && e.state != EventLog.State.NODATA) items += Item("back", e.t, null, null)
            if (present && e.before == EventLog.State.EMPTY) items += Item("arrive", e.t, null, e.zone)
            when (e.state) {
                EventLog.State.MOVING -> items += Item("move", e.t, end, e.zone)
                EventLog.State.EMPTY -> items += Item("empty", e.t, null, null)
                EventLog.State.OFFLINE -> items += Item("offline", e.t, end, null)
                else -> {}
            }
        }
        return segs to items.sortedByDescending { it.t }
    }
}
