package ru.wsn.capture

/**
 * The recording scenario: what the person does, where and for how long. Each step is sent to the server as a
 * "step" event; training (wsn.train) takes the zone from the phone's position and uses the "empty" steps (the
 * person leaves the flat with the phone) as "nobody home". Two empty steps, at the start and at the end, so the
 * honest check of training has "nobody home" on both sides of the walk.
 */
data class Step(val name: String, val text: String, val activity: String, val room: String?, val seconds: Int)

object Scenario {
    const val EMPTY_S = 120
    const val WALK_S = 45
    const val STAND_S = 15
    const val SIT_S = 15
    const val TOUR_S = 90
    const val WALK2_S = 30

    fun build(rooms: List<String>): List<Step> {
        val s = mutableListOf<Step>()
        s += Step("empty1", "Выйдите из квартиры с телефоном и закройте дверь. Вернитесь по сигналу.", "empty", null, EMPTY_S)
        for (r in rooms) {
            s += Step("walk:$r", "«$r»: медленно ходите по всей комнате — вдоль стен и через середину.", "walk", r, WALK_S)
            s += Step("stand:$r", "«$r»: встаньте посреди комнаты и стойте спокойно.", "stand", r, STAND_S)
            s += Step("sit:$r", "«$r»: сядьте (стул, диван, кровать) и посидите спокойно.", "sit", r, SIT_S)
        }
        s += Step("tour", "Пройдите по всем комнатам подряд, заходя в каждую.", "walk", null, TOUR_S)
        for (r in rooms.reversed()) s += Step("walk2:$r", "«$r»: ещё раз походите по комнате.", "walk", r, WALK2_S)
        s += Step("empty2", "Снова выйдите из квартиры с телефоном, закройте дверь и вернитесь по сигналу.", "empty", null, EMPTY_S)
        return s
    }

    fun totalSeconds(steps: List<Step>): Int = steps.sumOf { it.seconds }
}

/** Runs the steps one second at a time (driven by the activity's timer); pure, so it is unit-tested. */
class ScenarioRunner(val steps: List<Step>, private val onStep: (Step, Int) -> Unit, private val onDone: () -> Unit) {
    var index = -1
        private set
    var left = 0
        private set
    var paused = false
    val done: Boolean get() = index >= steps.size
    val current: Step? get() = steps.getOrNull(index)

    fun start() { if (index < 0) next() }

    /** One second passed. */
    fun tick() {
        if (paused || index < 0 || done) return
        left -= 1
        if (left <= 0) next()
    }

    fun skip() { if (!done && index >= 0) next() }

    private fun next() {
        index += 1
        if (done) { left = 0; onDone(); return }
        left = steps[index].seconds
        onStep(steps[index], index)
    }
}
