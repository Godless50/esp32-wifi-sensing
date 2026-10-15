package ru.wsn.capture.core

import kotlin.math.hypot
import kotlin.math.max

/**
 * The person's dot on the plan, cleaned up once a second. The model places the person by the nearest
 * fingerprints of the walk (ZoneModel.locate); a single noisy window can put them on the other side of the flat.
 * Here such jumps are cut off:
 *  - median of the last three placements: a one-second spike never reaches the dot;
 *  - the first dot (and the first after a pause) appears only when two placements agree;
 *  - speed gate: a person walks at most [vmax] m/s; a farther jump is held back as "pending";
 *  - it is believed only after [confirm] placements in a row agree on the same new spot (someone really is there);
 *  - when the room classifier is sure, the placement is kept inside that room;
 *  - smoothing: while the person moves the dot follows quickly, while they are still it barely drifts;
 *  - the dot always stays inside the plan; a second of "nobody" only hides it, a few seconds forget it.
 * Pure Kotlin, unit-tested.
 */
class DotTracker(
    private val vmax: Double = 1.5,
    private val slack: Double = 0.6,
    private val confirm: Int = 3,
    private val agreeMin: Double = 1.2,
    private val staleS: Double = 6.0,
    private val forgetAfter: Int = 4,
) {
    data class Dot(val x: Double, val y: Double, val r: Double, val room: String?)

    var dot: Dot? = null
        private set
    /** Placements thrown away as jumps since the start (shown nowhere; for tests and logs). */
    var rejected = 0
        private set

    private var lastT = Double.NaN
    private val recent = ArrayDeque<DoubleArray>()
    private val pending = ArrayList<DoubleArray>()
    private var near = 0
    private var absent = 0

    fun reset() { dot = null; recent.clear(); pending.clear(); lastT = Double.NaN; near = 0; absent = 0 }

    /** One second: present = somebody is home, moving = P(moving), fix = the model's raw placement,
     *  rooms = the plan, err = typical placement error (metres, NaN if unknown),
     *  sureRoom = the room the classifier is sure about (the placement is kept inside it), or null. */
    fun update(t: Double, present: Boolean, moving: Double, fix: ZoneModel.Fix?, rooms: List<TrainPipeline.Room>, err: Double,
               sureRoom: TrainPipeline.Room? = null): Dot? {
        if (rooms.isEmpty()) { reset(); return null }
        if (!present || fix == null) {                       // секунда «никого» только прячет точку
            if (++absent >= forgetAfter) reset()
            return null
        }
        absent = 0
        if (!lastT.isNaN() && t - lastT > staleS) reset()          // долго не было данных — начинаем заново
        val dt = if (lastT.isNaN()) 1.0 else (t - lastT).coerceIn(0.2, staleS)
        lastT = t
        val e = if (err.isNaN()) 1.0 else err
        val agree = max(agreeMin, 1.5 * e)

        var p = doubleArrayOf(fix.x, fix.y)
        if (sureRoom != null && !TrainPipeline.inside(p, sureRoom.poly)) p = clamp(p, listOf(sureRoom))
        recent.addLast(p); while (recent.size > 3) recent.removeFirst()
        val raw = clamp(median(recent), rooms)
        val r = max(0.35, max(e, minOf(fix.spread, 2.5)))
        val cur = dot
        if (cur == null) {
            // первая точка — только когда два последних места согласны
            if (recent.size < 2) return null
            val a = recent.elementAt(recent.size - 1); val b = recent.elementAt(recent.size - 2)
            if (hypot(a[0] - b[0], a[1] - b[1]) > agree) return null
            pending.clear(); return set(raw, r, rooms)
        }

        val d = hypot(raw[0] - cur.x, raw[1] - cur.y)
        if (d > vmax * max(dt, 1.0) + slack) {
            near = 0
            pending.add(raw); while (pending.size > confirm) pending.removeAt(0)
            if (pending.size >= confirm) {
                val mx = pending.sumOf { it[0] } / pending.size; val my = pending.sumOf { it[1] } / pending.size
                if (pending.all { hypot(it[0] - mx, it[1] - my) <= agree }) {   // несколько секунд подряд — там правда кто-то
                    pending.clear()
                    return set(clamp(doubleArrayOf(mx, my), rooms), r, rooms)
                }
            }
            rejected++
            return cur.copy(r = r)
        }
        if (++near >= 2) pending.clear()                      // одна близкая точка не стирает уже копящийся переход
        val a = if (moving >= 0.5) 0.5 else 0.15
        return set(clamp(doubleArrayOf(cur.x + a * (raw[0] - cur.x), cur.y + a * (raw[1] - cur.y)), rooms), r, rooms)
    }

    private fun set(p: DoubleArray, r: Double, rooms: List<TrainPipeline.Room>): Dot {
        val room = TrainPipeline.roomAt(p, rooms)?.let { rooms[it].name }
        return Dot(p[0], p[1], r, room).also { dot = it }
    }

    companion object {
        fun median(pts: Collection<DoubleArray>): DoubleArray {
            fun med(v: List<Double>): Double { val s = v.sorted(); val n = s.size; return if (n % 2 == 1) s[n / 2] else (s[n / 2 - 1] + s[n / 2]) / 2 }
            return doubleArrayOf(med(pts.map { it[0] }), med(pts.map { it[1] }))
        }

        /** A point outside every room is pulled onto the nearest wall and a little inside. */
        fun clamp(p: DoubleArray, rooms: List<TrainPipeline.Room>): DoubleArray {
            if (rooms.any { TrainPipeline.inside(p, it.poly) }) return p
            var best: DoubleArray? = null; var bd = Double.MAX_VALUE; var bestRoom: TrainPipeline.Room? = null
            for (rm in rooms) for (i in rm.poly.indices) {
                val a = rm.poly[i]; val b = rm.poly[(i + 1) % rm.poly.size]
                val dx = b[0] - a[0]; val dy = b[1] - a[1]; val l2 = dx * dx + dy * dy
                val u = if (l2 == 0.0) 0.0 else (((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2).coerceIn(0.0, 1.0)
                val q = doubleArrayOf(a[0] + u * dx, a[1] + u * dy)
                val d = hypot(p[0] - q[0], p[1] - q[1])
                if (d < bd) { bd = d; best = q; bestRoom = rm }
            }
            val q = best ?: return p
            val rm = bestRoom!!
            val cx = rm.poly.sumOf { it[0] } / rm.poly.size; val cy = rm.poly.sumOf { it[1] } / rm.poly.size
            val l = hypot(cx - q[0], cy - q[1])
            if (l < 1e-6) return q
            val k = minOf(0.25, l * 0.5) / l
            val inner = doubleArrayOf(q[0] + (cx - q[0]) * k, q[1] + (cy - q[1]) * k)
            return if (TrainPipeline.inside(inner, rm.poly)) inner else q   // у Г-образной комнаты центр бывает снаружи
        }
    }
}
