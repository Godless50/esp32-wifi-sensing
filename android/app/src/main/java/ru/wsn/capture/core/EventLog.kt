package ru.wsn.capture.core

import org.json.JSONArray
import org.json.JSONObject

/** The hub's diary for the main screen («События за сегодня»): changes of the home state, not every verdict.
 *  A new state is written only after it has held for a while, so a one-second flicker never becomes an event;
 *  «никого дома» must hold longer than the rest (a person sitting still can look empty for a few seconds).
 *  The event carries the time the new state began and the state before it, so the text can say «пришли». */
class EventLog(private val holdS: Double = 10.0, private val emptyHoldS: Double = 60.0, private val keep: Int = 200) {
    enum class State { EMPTY, PRESENT, OFFLINE }
    data class Event(val t: Double, val state: State, val zone: String?, val before: State?)

    private var shown: Pair<State, String?>? = null
    private var cand: Pair<State, String?>? = null
    private var candT = 0.0
    private val list = ArrayDeque<Event>()

    @Synchronized fun events(): List<Event> = list.toList()   // newest first

    /** Feed the state once per tick; returns the event when one is written. */
    @Synchronized fun feed(t: Double, state: State, zone: String? = null): Event? {
        val s = state to (if (state == State.PRESENT) zone else null)
        if (s == shown) { cand = null; return null }
        if (s != cand) { cand = s; candT = t; return null }
        if (t - candT < if (state == State.EMPTY) emptyHoldS else holdS) return null
        val e = Event(candT, s.first, s.second, shown?.first)
        shown = s; cand = null
        list.addFirst(e)
        while (list.size > keep) list.removeLast()
        return e
    }

    /** Human text of an event, as the main screen shows it. */
    fun text(e: Event): String = when (e.state) {
        State.OFFLINE -> "Нет связи с жучком — данных нет"
        State.EMPTY -> "Никого дома"
        State.PRESENT -> when (e.before) {
            State.EMPTY -> "Кто-то пришёл" + (e.zone?.let { " · $it" } ?: "")
            State.OFFLINE -> "Связь вернулась · кто-то дома"
            null -> "Кто-то дома" + (e.zone?.let { " · $it" } ?: "")
            else -> "Движение" + (e.zone?.let { " · $it" } ?: "")
        }
    }

    @Synchronized fun toJson(): String = JSONArray().apply {
        for (e in list) put(JSONObject().put("t", e.t).put("s", e.state.name).put("z", e.zone ?: JSONObject.NULL)
            .put("b", e.before?.name ?: JSONObject.NULL))
    }.toString()

    /** Restore after a restart; the last written state counts as shown, so it is not written twice. */
    @Synchronized fun load(json: String) {
        list.clear()
        val a = try { JSONArray(json) } catch (e: Exception) { return }
        for (i in 0 until minOf(a.length(), keep)) try {
            val o = a.getJSONObject(i)
            list.addLast(Event(o.getDouble("t"), State.valueOf(o.getString("s")), o.optString("z").takeIf { !o.isNull("z") && it.isNotEmpty() },
                if (o.isNull("b")) null else State.valueOf(o.getString("b"))))
        } catch (_: Exception) {}
        shown = list.firstOrNull()?.let { it.state to it.zone }
    }
}
