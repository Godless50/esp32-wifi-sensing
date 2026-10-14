package ru.wsn.capture.core

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Random
import kotlin.math.cos
import kotlin.math.sin

/** The phone's training pipeline end to end on a synthetic walk: two rooms, the phone left at home on the
 *  "empty" steps (it lies in room A, yet those windows must be "nobody home"), links whose amplitude jitter
 *  depends on where the person is. */
class TrainPipelineTest {
    private val hz = 20.0

    private fun step(t: Double, name: String, activity: String, room: String?) =
        JSONObject().put("kind", "step").put("t", t).put("name", name).put("activity", activity).put("room", room ?: JSONObject.NULL)

    private fun corner(x: Double, y: Double) = JSONObject().put("kind", "corner").put("p", JSONArray(listOf(x, 0.0, -y)))

    /** roomsInfo: whether links 0/1 react to the person's room (else every link only sees "somebody"). */
    private fun session(roomsInfo: Boolean, seed: Long = 7): TrainPipeline.Session {
        val r = Random(seed)
        val t0 = 1000.0
        // empty 0-150 (телефон лежит в A), A 150-380, B 380-610, empty 610-760
        val plan = listOf(Triple(0.0, "empty", null), Triple(150.0, "walk", "A"), Triple(380.0, "walk", "B"),
            Triple(610.0, "empty", null))
        val t1 = t0 + 760.0
        val events = ArrayList<JSONObject>()
        events += JSONObject().put("kind", "room_new").put("name", "A")
        listOf(0.0 to 0.0, 4.0 to 0.0, 4.0 to 4.0, 0.0 to 4.0).forEach { events += corner(it.first, it.second) }
        events += JSONObject().put("kind", "room_close")
        events += JSONObject().put("kind", "room_new").put("name", "B")
        listOf(4.0 to 0.0, 8.0 to 0.0, 8.0 to 4.0, 4.0 to 4.0).forEach { events += corner(it.first, it.second) }
        events += JSONObject().put("kind", "room_close")
        plan.forEachIndexed { i, (s, a, room) -> events += step(t0 + s, "s$i", a, room) }
        fun where(t: Double): Pair<String?, DoubleArray> {
            val s = t - t0
            val cur = plan.last { it.first <= s }
            return when (cur.third) {
                null -> null to doubleArrayOf(2.0, 2.0)                              // телефон лежит в A, людей нет
                "A" -> "A" to doubleArrayOf(2.0 + 1.5 * cos(s / 5), 2.0 + 1.5 * sin(s / 3))
                else -> "B" to doubleArrayOf(6.0 + 1.5 * cos(s / 5), 2.0 + 1.5 * sin(s / 3))
            }
        }
        val traj = ArrayList<DoubleArray>()
        var t = t0; while (t < t1) { val (_, xy) = where(t); traj += doubleArrayOf(t, xy[0], xy[1]); t += 0.2 }
        val links = listOf("aa:aa:aa:aa:aa:01|ap", "aa:aa:aa:aa:aa:01|b2", "aa:aa:aa:aa:aa:02|ap")
        val base = DoubleArray(64) { 20.0 + 5.0 * sin(it / 7.0) }
        val frames = links.mapIndexed { j, lk ->
            val ts = ArrayList<Double>(); val amps = ArrayList<DoubleArray>()
            var tt = t0
            while (tt < t1) {
                val room = where(tt).first
                val level = when {
                    room == null -> 0.02
                    !roomsInfo -> 0.10
                    j == 0 && room == "A" -> 0.15
                    j == 1 && room == "B" -> 0.15
                    j == 2 -> 0.08
                    else -> 0.04
                }
                ts += tt; amps += DoubleArray(64) { base[it] * (1.0 + level * r.nextGaussian()) }
                tt += 1.0 / hz
            }
            lk to (ts.toDoubleArray() to amps.toTypedArray())
        }.toMap()
        return TrainPipeline.Session("s1", "walk", t0, t1, events, traj.toTypedArray(), frames)
    }

    private val fast = Trainer.FitConfig(epochs = 25)

    @Test fun twoRoomsAreLearntAndEmptyStepsAreNobodyHome() {
        val s = session(roomsInfo = true)
        val rooms = TrainPipeline.rooms(s.events)
        assertEquals(listOf("A", "B"), rooms.map { it.name })
        val ends = TrainPipeline.windowEnds(s)
        val (z, m) = TrainPipeline.labels(s, rooms, ends)
        val i = ends.indexOfFirst { it > 1000.0 + 100 }   // шаг «пусто», телефон в A: метка «пусто», а не A
        assertEquals(1.0, z[i][2], 0.0); assertEquals(0.0, z[i][0], 0.0); assertEquals(0.0, m[i], 0.0)
        val res = TrainPipeline.train(s, nets = 2, folds = 3, cfg = fast)
        assertTrue(res.message, res.roomsOk)
        assertTrue(res.metrics.toString(), res.metrics.getDouble("nobody_home_bal_acc") > 0.9)
        val model = ZoneModel.fromJson(res.modelJson)
        assertEquals(listOf("A", "B", TrainPipeline.EMPTY), model.zones)
        val j = JSONObject(res.modelJson)
        assertTrue(j.getBoolean("rooms_ok"))
        assertEquals(3, j.getJSONObject("quiet").length())
    }

    @Test fun linksBlindToRoomsGiveTheFlatAsOneZone() {
        val res = TrainPipeline.train(session(roomsInfo = false), nets = 2, folds = 3, cfg = fast)
        assertFalse(res.message, res.roomsOk)
        assertTrue(res.metrics.toString(), res.metrics.getDouble("nobody_home_bal_acc") > 0.9)  // «кто-то / никого» остаётся
    }

    @Test fun noRoomsRefused() {
        val s = session(true)
        val bare = TrainPipeline.Session(s.id, s.kind, s.t0, s.t1, s.events.filter { it.optString("kind") == "step" }, s.traj, s.frames)
        try { TrainPipeline.train(bare, nets = 1, folds = 2, cfg = fast); throw AssertionError("must refuse") }
        catch (e: TrainPipeline.TrainError) {}
    }
}
