package ru.wsn.capture.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import ru.wsn.capture.core.EventLog.State

class EventLogTest {
    @Test fun flickerIsNotAnEventAndEmptyHoldsLonger() {
        val log = EventLog(holdS = 10.0, emptyHoldS = 60.0)
        for (t in 0..20) log.feed(t.toDouble(), State.PRESENT)
        assertEquals(1, log.events().size)
        assertEquals(0.0, log.events()[0].t, 0.0)          // the time it began, not when it was confirmed
        for (t in 21..25) log.feed(t.toDouble(), State.EMPTY)  // 5 s of «empty»: somebody sitting still
        for (t in 26..40) log.feed(t.toDouble(), State.PRESENT)
        assertEquals(1, log.events().size)
        for (t in 41..110) log.feed(t.toDouble(), State.EMPTY)
        assertEquals(State.EMPTY, log.events()[0].state)
        assertEquals("Никого дома", log.text(log.events()[0]))
    }

    @Test fun textsSayWhatHappened() {
        val log = EventLog(holdS = 1.0, emptyHoldS = 1.0)
        fun hold(t0: Int, s: State, z: String? = null) { for (t in t0..t0 + 2) log.feed(t.toDouble(), s, z) }
        hold(0, State.EMPTY); hold(10, State.PRESENT, "Кухня"); hold(20, State.PRESENT, "Зал"); hold(30, State.OFFLINE); hold(40, State.PRESENT, "Зал")
        assertEquals(listOf("Связь вернулась · кто-то дома", "Нет связи с жучком — данных нет", "Движение · Зал", "Кто-то пришёл · Кухня", "Никого дома"),
            log.events().map { log.text(it) })
    }

    @Test fun restoresWithoutRepeatingTheLastState() {
        val a = EventLog(holdS = 1.0)
        for (t in 0..3) a.feed(t.toDouble(), State.PRESENT, "Кухня")
        val b = EventLog(holdS = 1.0)
        b.load(a.toJson())
        assertEquals(a.events(), b.events())
        for (t in 4..10) assertNull(b.feed(t.toDouble(), State.PRESENT, "Кухня"))
    }
}
