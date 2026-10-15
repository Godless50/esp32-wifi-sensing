package ru.wsn.capture.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import ru.wsn.capture.core.EventLog.State

class EventLogTest {
    @Test fun flickerIsNotAnEventAndEmptyHoldsLonger() {
        val log = EventLog(holdS = 10.0, emptyHoldS = 60.0)
        for (t in 0..20) log.feed(t.toDouble(), State.QUIET)
        assertEquals(1, log.events().size)
        assertEquals(0.0, log.events()[0].t, 0.0)          // the time it began, not when it was confirmed
        for (t in 21..25) log.feed(t.toDouble(), State.EMPTY)  // 5 s of «empty»: somebody sitting still
        for (t in 26..40) log.feed(t.toDouble(), State.QUIET)
        assertEquals(1, log.events().size)
        for (t in 41..110) log.feed(t.toDouble(), State.EMPTY)
        assertEquals(State.EMPTY, log.events()[0].state)
    }

    @Test fun zoneOnlyWhilePresent() {
        val log = EventLog(holdS = 1.0, emptyHoldS = 1.0)
        for (t in 0..2) log.feed(t.toDouble(), State.EMPTY, "Кухня")
        assertNull(log.events()[0].zone)
        for (t in 10..12) log.feed(t.toDouble(), State.MOVING, "Кухня")
        assertEquals("Кухня", log.events()[0].zone)
    }

    @Test fun restoresWithoutRepeatingTheLastStateAndReadsTheOldFormat() {
        val a = EventLog(holdS = 1.0)
        for (t in 0..3) a.feed(t.toDouble(), State.MOVING, "Кухня")
        val b = EventLog(holdS = 1.0)
        b.load(a.toJson())
        assertEquals(a.events(), b.events())
        for (t in 4..10) assertNull(b.feed(t.toDouble(), State.MOVING, "Кухня"))
        val old = EventLog(); old.load("""[{"t":5,"s":"PRESENT","z":null,"b":"EMPTY"}]""")
        assertEquals(State.QUIET, old.events()[0].state)
    }

    @Test fun dayLineAndWords() {
        val log = EventLog(holdS = 1.0, emptyHoldS = 1.0)
        val d = 1_000_000.0                                   // начало дня
        fun hold(h: Double, s: State, z: String? = null) { for (k in 0..2) log.feed(d + h * 3600 + k, s, z) }
        hold(-1.0, State.QUIET)                               // вчера вечером кто-то был
        hold(2.0, State.OFFLINE); hold(2.5, State.QUIET)      // жучок молчал полчаса
        hold(9.0, State.EMPTY); hold(14.0, State.QUIET)       // ушли и пришли
        hold(17.5, State.MOVING, "Кухня"); hold(17.7, State.QUIET)
        val (segs, items) = HomeDay.build(log.events(), d, d + 18 * 3600)
        assertEquals(listOf("quiet", "offline", "quiet", "empty", "quiet", "moving", "quiet"), segs.map { it.state })
        assertEquals(0.0, segs.first().from, 1e-9); assertEquals(18.0, segs.last().to, 1e-9)
        assertEquals(listOf("move", "arrive", "empty", "back", "offline"), items.map { it.kind })
        val move = items.first()
        assertEquals("Кухня", move.zone); assertEquals(d + 17.7 * 3600, move.t2!!, 1e-6)
        assertEquals(d + 2.5 * 3600, items.last().t2!!, 1e-6)
    }
}
