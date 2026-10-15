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

    /** «Все за дверь»: n = 1 в начале, 2 в конце. phoneStays — телефон-центр остаётся дома и слушает. */
    fun leave(n: Int, phoneStays: Boolean): Step {
        val again = if (n > 1) "Снова " else ""
        val text = if (phoneStays) "${again}положите телефон здесь, выйдите из квартиры и закройте дверь. Вернитесь через ${EMPTY_S / 60} минуты."
                   else "${again}выйдите из квартиры с телефоном и закройте дверь. Вернитесь по сигналу."
        return Step("empty$n", text.replaceFirstChar { it.uppercase() }, "empty", null, EMPTY_S)
    }

    /** phoneStays: the phone is the hub and must keep receiving, so on the empty steps it stays at home. */
    fun build(rooms: List<String>, phoneStays: Boolean = false): List<Step> {
        val s = mutableListOf<Step>()
        s += leave(1, phoneStays)
        for (r in rooms) {
            s += Step("walk:$r", "«$r»: медленно ходите по всей комнате — вдоль стен и через середину.", "walk", r, WALK_S)
            s += Step("stand:$r", "«$r»: встаньте посреди комнаты и стойте спокойно.", "stand", r, STAND_S)
            s += Step("sit:$r", "«$r»: сядьте (стул, диван, кровать) и посидите спокойно.", "sit", r, SIT_S)
        }
        s += Step("tour", "Пройдите по всем комнатам подряд, заходя в каждую.", "walk", null, TOUR_S)
        for (r in rooms.reversed()) s += Step("walk2:$r", "«$r»: ещё раз походите по комнате.", "walk", r, WALK2_S)
        s += leave(2, phoneStays)
        return s
    }

    fun totalSeconds(steps: List<Step>): Int = steps.sumOf { it.seconds }
}

/** Runs the steps one second at a time (driven by the activity's timer); pure, so it is unit-tested.
 *  gate(step, previous) — шаг ждёт человека: таймер стоит, onStep не вызывается, пока не позовут [go]
 *  (например, «Уходим» перед «все за дверь» и «Дальше», когда вернулись). onWait — шаг начал ждать. */
class ScenarioRunner(
    val steps: List<Step>,
    private val onStep: (Step, Int) -> Unit,
    private val onDone: () -> Unit,
    private val gate: (Step, Step?) -> Boolean = { _, _ -> false },
    private val onWait: (Step, Int) -> Unit = { _, _ -> },
) {
    var index = -1
        private set
    var left = 0
        private set
    var paused = false
    var waiting = false
        private set
    val done: Boolean get() = index >= steps.size
    val current: Step? get() = steps.getOrNull(index)

    fun start() { if (index < 0) next() }

    /** One second passed. */
    fun tick() {
        if (paused || waiting || index < 0 || done) return
        left -= 1
        if (left <= 0) next()
    }

    fun skip() { if (!done && index >= 0) next() }

    /** The person is ready: the waiting step starts now. */
    fun go() {
        if (!waiting || done) return
        waiting = false; paused = false
        onStep(steps[index], index)
    }

    /** The current step from the beginning (someone stayed at home, «эту комнату заново»). */
    fun again() {
        if (done || index < 0) return
        left = steps[index].seconds; paused = false
        if (gate(steps[index], steps.getOrNull(index - 1))) { waiting = true; onWait(steps[index], index) }
        else { waiting = false; onStep(steps[index], index) }
    }

    private fun next() {
        index += 1
        waiting = false
        if (done) { left = 0; onDone(); return }
        left = steps[index].seconds
        if (gate(steps[index], steps.getOrNull(index - 1))) { waiting = true; onWait(steps[index], index) }
        else onStep(steps[index], index)
    }
}
