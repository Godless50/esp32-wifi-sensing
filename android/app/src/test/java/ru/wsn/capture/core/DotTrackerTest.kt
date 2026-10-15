package ru.wsn.capture.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DotTrackerTest {
    private val rooms = listOf(
        TrainPipeline.Room("Зал", listOf(doubleArrayOf(0.0, 0.0), doubleArrayOf(4.0, 0.0), doubleArrayOf(4.0, 4.0), doubleArrayOf(0.0, 4.0))),
        TrainPipeline.Room("Кухня", listOf(doubleArrayOf(4.0, 0.0), doubleArrayOf(8.0, 0.0), doubleArrayOf(8.0, 4.0), doubleArrayOf(4.0, 4.0))),
    )
    private fun fix(x: Double, y: Double) = ZoneModel.Fix(x, y, 0.3)

    @Test fun aNoisyJumpToTheOtherRoomIsCutOff() {
        val t = DotTracker()
        var s = 0.0
        repeat(5) { t.update(s++, true, 0.1, fix(1.0, 1.0), rooms, 0.8) }
        t.update(s++, true, 0.1, fix(7.0, 3.0), rooms, 0.8)          // одна секунда шума
        t.update(s++, true, 0.1, fix(1.0, 1.0), rooms, 0.8)
        val d = t.dot!!
        assertEquals("Зал", d.room)
        assertTrue(d.x < 2.0 && d.y < 2.0)
    }

    @Test fun twoSpikesInARowAreHeldBackByTheSpeedGate() {
        val t = DotTracker()
        var s = 0.0
        repeat(5) { t.update(s++, true, 0.9, fix(1.0, 1.0), rooms, 0.8) }
        t.update(s++, true, 0.9, fix(7.5, 3.5), rooms, 0.8)
        t.update(s++, true, 0.9, fix(7.5, 3.5), rooms, 0.8)
        assertEquals("Зал", t.dot!!.room)
        assertTrue(t.rejected >= 1)
    }

    @Test fun aPersonWhoReallyIsThereIsBelievedAfterAFewSeconds() {
        val t = DotTracker()
        var s = 0.0
        repeat(5) { t.update(s++, true, 0.9, fix(1.0, 1.0), rooms, 0.8) }
        repeat(6) { t.update(s++, true, 0.9, fix(7.0, 2.0), rooms, 0.8) }
        assertEquals("Кухня", t.dot!!.room)
    }

    @Test fun aStillPersonBarelyDriftsAndTheDotStaysInsideThePlan() {
        val t = DotTracker()
        var s = 0.0
        repeat(5) { t.update(s++, true, 0.1, fix(2.0, 2.0), rooms, 0.8) }
        t.update(s++, true, 0.1, fix(2.8, 2.0), rooms, 0.8)
        t.update(s++, true, 0.1, fix(2.8, 2.0), rooms, 0.8)
        assertTrue(t.dot!!.x < 2.4)
        val out = DotTracker.clamp(doubleArrayOf(-3.0, 2.0), rooms)
        assertTrue(rooms.any { TrainPipeline.inside(out, it.poly) })
    }

    @Test fun wallsEditedByHandReplaceTheMarkedCorners() {
        fun ev(kind: String) = org.json.JSONObject().put("kind", kind)
        fun corner(x: Double, z: Double) = ev("corner").put("p", org.json.JSONArray(listOf(x, 0.0, z)))
        val events = listOf(ev("room_new").put("name", "Зал"), corner(0.0, 0.0), corner(3.9, 0.1), corner(4.1, -3.8), corner(0.1, -4.2),
            ev("room_close"),
            ev("room_edit").put("name", "Зал").put("poly", org.json.JSONArray("[[0,0],[4,0],[4,4],[0,4]]")))
        val r = TrainPipeline.rooms(events)
        assertEquals(1, r.size)
        assertEquals(4.0, r[0].poly[1][0], 1e-9)
        assertEquals(4.0, r[0].poly[2][1], 1e-9)
    }

    @Test fun theNearestFingerprintsPlaceThePerson() {
        val e = arrayOf(doubleArrayOf(0.0, 0.0), doubleArrayOf(0.1, 0.0), doubleArrayOf(5.0, 5.0), doubleArrayOf(5.1, 5.0))
        val p = arrayOf(doubleArrayOf(1.0, 1.0), doubleArrayOf(1.2, 1.0), doubleArrayOf(6.0, 2.0), doubleArrayOf(6.2, 2.0))
        val f = ZoneModel.nearest(doubleArrayOf(0.05, 0.0), e, p, 2, null)!!
        assertEquals(1.1, f.x, 0.05)
        val g = ZoneModel.nearest(doubleArrayOf(0.05, 0.0), e, p, 2) { it < 2 }!!   // свои соседи исключены
        assertTrue(g.x > 5.9)
    }

    @Test fun nobodyHomeOrNoPlacementHidesTheDot() {
        val t = DotTracker()
        assertNull(t.update(0.0, true, 0.5, fix(1.0, 1.0), rooms, 0.8))        // одно место — ещё не точка
        assertNotNull(t.update(1.0, true, 0.5, fix(1.1, 1.0), rooms, 0.8))
        assertNull(t.update(2.0, false, 0.5, fix(1.0, 1.0), rooms, 0.8))
        assertNotNull(t.dot)                                                    // секунда «никого» только прячет
        repeat(4) { t.update(3.0 + it, false, 0.5, null, rooms, 0.8) }
        assertNull(t.dot)                                                       // несколько секунд — забыли
    }

    @Test fun afterAPauseANoisyFirstPlacementIsNotShown() {
        val t = DotTracker()
        assertNull(t.update(0.0, true, 0.5, fix(1.0, 1.0), rooms, 0.8))
        assertNull(t.update(1.0, true, 0.5, fix(7.5, 3.5), rooms, 0.8))        // два места не согласны
    }

    @Test fun theClassifiersSureRoomKeepsTheDotInIt() {
        val t = DotTracker()
        var s = 0.0
        repeat(5) { t.update(s++, true, 0.9, fix(3.8, 2.0), rooms, 0.8, sureRoom = rooms[1]) }
        assertEquals("Кухня", t.dot!!.room)
    }
}
