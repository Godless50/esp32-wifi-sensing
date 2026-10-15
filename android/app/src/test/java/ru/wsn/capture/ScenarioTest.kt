package ru.wsn.capture

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ScenarioTest {
    @Test fun everyRoomIsWalkedStoodAndSatInAndEmptyIsOnBothEnds() {
        val s = Scenario.build(listOf("зал", "кухня"))
        assertEquals("empty", s.first().activity)
        assertEquals("empty", s.last().activity)
        for (r in listOf("зал", "кухня")) {
            assertTrue(s.any { it.room == r && it.activity == "walk" })
            assertTrue(s.any { it.room == r && it.activity == "stand" })
            assertTrue(s.any { it.room == r && it.activity == "sit" })
        }
        assertTrue(Scenario.totalSeconds(s) in 8 * 60..20 * 60)
    }

    @Test fun runnerAdvancesPausesSkipsAndFinishes() {
        val started = mutableListOf<String>()
        var done = false
        val steps = listOf(Step("a", "", "walk", "x", 2), Step("b", "", "stand", "x", 1), Step("c", "", "sit", "x", 3))
        val r = ScenarioRunner(steps, { st, _ -> started += st.name }, { done = true })
        r.tick()  // до start ничего не идёт
        assertEquals(emptyList<String>(), started)
        r.start()
        assertEquals(listOf("a"), started)
        r.paused = true; r.tick(); r.tick()
        assertEquals(2, r.left)
        r.paused = false; r.tick(); r.tick()
        assertEquals(listOf("a", "b"), started)
        r.skip()
        assertEquals(listOf("a", "b", "c"), started)
        r.tick(); r.tick(); r.tick()
        assertTrue(done && r.done)
        r.tick(); r.skip()  // после конца — ничего
        assertEquals(3, started.size)
    }

    @Test fun gatedStepsWaitForThePersonAndCanStartAgain() {
        val started = mutableListOf<String>(); val waited = mutableListOf<String>()
        val steps = listOf(Step("out", "", "empty", null, 2), Step("a", "", "walk", "x", 2))
        val r = ScenarioRunner(steps, { st, _ -> started += st.name }, {},
            gate = { st, prev -> st.activity == "empty" || prev?.activity == "empty" }, onWait = { st, _ -> waited += st.name })
        r.start()
        assertTrue(r.waiting); assertEquals(listOf("out"), waited); assertEquals(emptyList<String>(), started)
        r.tick(); r.tick()
        assertEquals(2, r.left)                       // ждёт — время не идёт
        r.go(); r.tick()
        assertEquals(listOf("out"), started); assertEquals(1, r.left)
        r.again()                                     // кто-то остался: снова ждём «Уходим»
        assertTrue(r.waiting); assertEquals(2, r.left)
        r.go(); r.tick(); r.tick()
        assertTrue(r.waiting); assertEquals("a", r.current?.name)   // после «за дверь» — ждём «Дальше»
        r.go()
        assertEquals(listOf("out", "out", "a"), started)
    }
}
