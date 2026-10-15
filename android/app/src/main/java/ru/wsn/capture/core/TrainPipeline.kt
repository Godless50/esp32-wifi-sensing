package ru.wsn.capture.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.DataInputStream
import java.io.EOFException
import java.io.File
import java.util.Random
import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.sqrt

/** Training of the room model on the phone from one walk this phone recorded as the hub (LocalCapture session +
 *  the raw datagrams of that time). The same pipeline as server/wsn/train.py — windows of 4 s every second, link
 *  statistics, quiet levels from the "nobody home" windows, honest blocked-time check, an ensemble of MLPs — with
 *  two differences that come from the phone being the hub:
 *  - the plan is the walk's own AR floor (no global axes are needed: the newest walk alone is trained on);
 *  - on "empty" steps the phone stays at home (it must keep receiving), so those windows are "nobody home"
 *    whatever its position.
 *  Rooms are offered only when the check shows the model tells them apart; otherwise the model still says
 *  "somebody / nobody, moving or not" and the map shows one zone (the flat). */
object TrainPipeline {
    const val EMPTY = "пусто"
    const val WIN_S = 4.0
    const val STEP_S = 1.0
    const val POS_LAG_S = 0.5
    const val POS_MAX_GAP_S = 1.5
    const val NEAR_PLAN_M = 0.6
    const val EMPTY_GRACE_S = 20.0
    const val LINK_MIN_SHARE = 0.3
    const val MIN_WINDOWS = 120
    const val MOVE_YES = 0.25
    const val MOVE_NO = 0.08
    const val FLOOR_Q = 10.0
    const val QUIET_MIN = 30
    const val DEDUPE_M = 0.25
    const val TRAJ_MIN_DT = 0.2
    const val MOVED_M = 0.5          // телефон сдвинули: человек вернулся
    const val RETURN_MAX_S = 300.0   // после шага «выйдите» ждём, что телефон возьмут, не дольше этого
    /** Rooms are shown when the held-out balanced room accuracy reaches this and beats chance clearly. */
    const val ROOMS_MIN_ACC = 0.6
    const val ROOMS_OVER_CHANCE = 0.2
    /** Fingerprints kept in the model for placing the person on the plan, and how they are checked. */
    const val FP_MAX = 900
    const val FP_K = 7
    const val FP_EXCLUDE_S = 30.0

    class TrainError(msg: String) : Exception(msg)

    data class Room(val name: String, val poly: List<DoubleArray>)

    class Session(
        val id: String, val kind: String, val t0: Double, val t1: Double,
        val events: List<JSONObject>,
        val traj: Array<DoubleArray>,                                   // t, x, y (floor), 5 Hz
        val frames: Map<String, Pair<DoubleArray, Array<DoubleArray>>>, // link -> (t, amplitude[64])
        val boardMarks: Map<String, DoubleArray> = emptyMap(),          // board MAC -> floor x, y
    )

    // ---------------- reading a recorded session ----------------
    fun floor(p: JSONArray) = doubleArrayOf(p.getDouble(0), -p.getDouble(2))   // AR (x, y up, z) -> plan (x, -z)

    private fun jsonl(f: File): List<JSONObject> = if (!f.exists()) emptyList() else
        f.readLines().mapNotNull { try { JSONObject(it) } catch (e: Exception) { null } }

    /** Raw records of the hub: u64 ms (LE), u16 length, datagram. Signatures were checked on arrival. */
    fun readRaw(f: File): Map<String, Pair<DoubleArray, Array<DoubleArray>>> {
        val acc = HashMap<String, Pair<ArrayList<Double>, ArrayList<DoubleArray>>>()
        if (!f.exists()) return emptyMap()
        val feed = FrameFeed()
        DataInputStream(f.inputStream().buffered()).use { inp ->
            val hdr = ByteArray(10)
            while (true) {
                try { inp.readFully(hdr) } catch (e: EOFException) { break }
                var ms = 0L; for (i in 0 until 8) ms = ms or ((hdr[i].toLong() and 0xFF) shl (8 * i))
                val len = (hdr[8].toInt() and 0xFF) or ((hdr[9].toInt() and 0xFF) shl 8)
                val buf = ByteArray(len)
                try { inp.readFully(buf) } catch (e: EOFException) { break }   // запись оборвалась на середине
                val p = try { Wsn1.parse(buf, null, verify = false) } catch (e: Exception) { continue }
                if (p.type != Wsn1.TYPE_CSI && p.type != Wsn1.TYPE_CSI_SRC) continue
                for (fr in feed.frames(p, ms / 1000.0)) {
                    val (ts, amps) = acc.getOrPut("${p.deviceId}|${fr.src}") { ArrayList<Double>() to ArrayList() }
                    ts.add(fr.tSec); amps.add(fr.amp)
                }
            }
        }
        return acc.mapValues { (_, v) ->
            val order = v.first.indices.sortedBy { v.first[it] }
            DoubleArray(order.size) { v.first[order[it]] } to Array(order.size) { v.second[order[it]] }
        }
    }

    fun loadSession(dir: File): Session {
        val meta = JSONObject(File(dir, "meta.json").readText())
        val events = jsonl(File(dir, "events.jsonl"))
        val traj = ArrayList<DoubleArray>()
        var last = Double.NEGATIVE_INFINITY
        for (p in jsonl(File(dir, "pose.jsonl")).sortedBy { it.optDouble("t") }) {
            val t = p.optDouble("t"); val pa = p.optJSONArray("p") ?: continue
            if (t - last < TRAJ_MIN_DT - 1e-9) continue
            val xy = floor(pa); traj.add(doubleArrayOf(t, xy[0], xy[1])); last = t
        }
        val marks = LinkedHashMap<String, DoubleArray>()
        for (e in events) if (e.optString("kind") == "device" && e.optString("role") == "board" && e.optJSONArray("p") != null)
            marks[e.optString("mac").lowercase()] = floor(e.getJSONArray("p"))
        val t0 = meta.getDouble("t0")
        val t1 = if (meta.has("t1")) meta.getDouble("t1") else (traj.lastOrNull()?.get(0) ?: t0)
        return Session(meta.getString("id"), meta.optString("kind", "walk"), t0, t1, events,
            traj.toTypedArray(), readRaw(File(dir, "csi.wsn")), marks)
    }

    // ---------------- plan (rooms of the walk) ----------------
    private fun area(p: List<DoubleArray>): Double {
        var s = 0.0; for (i in p.indices) { val a = p[i]; val b = p[(i + p.size - 1) % p.size]; s += a[0] * b[1] - b[0] * a[1] }
        return 0.5 * s
    }

    /** Drop repeated presses and order corners around their centre, as geometry.normalize_corners. */
    fun normalizeCorners(pts: List<DoubleArray>): List<DoubleArray> {
        val kept = ArrayList<DoubleArray>()
        for (p in pts) if (kept.all { hypot(it[0] - p[0], it[1] - p[1]) >= DEDUPE_M }) kept.add(p)
        if (kept.size < 3) return kept
        val cx = kept.sumOf { it[0] } / kept.size; val cy = kept.sumOf { it[1] } / kept.size
        return kept.sortedBy { atan2(it[1] - cy, it[0] - cx) }
    }

    fun rooms(events: List<JSONObject>): List<Room> {
        val out = LinkedHashMap<String, List<DoubleArray>>()
        var cur: Pair<String, MutableList<DoubleArray>>? = null
        fun finish() {
            val c = cur ?: return
            val poly = normalizeCorners(c.second)
            if (poly.size >= 3 && abs(area(poly)) >= 0.5) out[c.first] = poly
        }
        for (e in events) when (e.optString("kind")) {
            "room_new" -> { finish(); cur = e.optString("name", "комната") to ArrayList() }
            "corner" -> e.optJSONArray("p")?.let { p -> cur?.second?.add(floor(p)) }
            "room_close" -> { finish(); cur = null }
            // стены, поправленные вручную перед сохранением плана: заменяют то, что получилось из углов
            "room_edit" -> {
                finish(); cur = null
                val pts = e.optJSONArray("poly") ?: continue
                val poly = (0 until pts.length()).mapNotNull { i -> pts.optJSONArray(i)?.let { doubleArrayOf(it.getDouble(0), it.getDouble(1)) } }
                if (poly.size >= 3 && abs(area(poly)) >= 0.5) out[e.optString("name", "комната")] = poly
            }
        }
        finish()
        return out.map { Room(it.key, it.value) }
    }

    fun inside(pt: DoubleArray, poly: List<DoubleArray>): Boolean {
        var inside = false
        for (i in poly.indices) {
            val a = poly[i]; val b = poly[(i + 1) % poly.size]
            if ((a[1] > pt[1]) != (b[1] > pt[1]) && pt[0] < (b[0] - a[0]) * (pt[1] - a[1]) / (b[1] - a[1]) + a[0]) inside = !inside
        }
        return inside
    }

    private fun segDist(p: DoubleArray, a: DoubleArray, b: DoubleArray): Double {
        val dx = b[0] - a[0]; val dy = b[1] - a[1]; val l2 = dx * dx + dy * dy
        val t = if (l2 == 0.0) 0.0 else (((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2).coerceIn(0.0, 1.0)
        return hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)
    }

    /** Room index at a floor point: inside a room, or within NEAR_PLAN_M of its wall (standing by the wall). */
    fun roomAt(pt: DoubleArray, rooms: List<Room>): Int? {
        rooms.forEachIndexed { i, r -> if (inside(pt, r.poly)) return i }
        var best: Int? = null; var bd = NEAR_PLAN_M
        rooms.forEachIndexed { i, r ->
            for (k in r.poly.indices) { val d = segDist(pt, r.poly[k], r.poly[(k + 1) % r.poly.size]); if (d <= bd) { bd = d; best = i } }
        }
        return best
    }

    // ---------------- windows, labels, features ----------------
    fun windowEnds(s: Session): DoubleArray {
        val out = ArrayList<Double>(); var t = s.t0 + WIN_S + 1.0
        while (t < s.t1 - 0.5) { out.add(t); t += STEP_S }
        return out.toDoubleArray()
    }

    /** Z [windows][rooms + EMPTY]: allowed set (all zeros = unknown); M: moving 1 / 0 / -1 (unknown). */
    fun labels(s: Session, rooms: List<Room>, ends: DoubleArray): Pair<Array<DoubleArray>, DoubleArray> {
        val k = rooms.size + 1; val e = rooms.size
        val z = Array(ends.size) { DoubleArray(k) }; val m = DoubleArray(ends.size) { -1.0 }
        val steps = s.events.filter { it.optString("kind") == "step" && it.has("t") }.sortedBy { it.getDouble("t") }
        val tr = s.traj
        // Телефон-центр на шагах «выйдите» лежит дома. Человек уходит и возвращается не точно по шагу (сигнала за
        // дверью не слышно): пока он уходит (EMPTY_GRACE_S) и пока после шага телефон не взяли в руки — метки нет;
        // «никого» — от конца ухода до конца шага или до момента, когда телефон сдвинули (вернулся раньше).
        val emptyIv = ArrayList<Pair<Double, Double>>(); val unknownIv = ArrayList<Pair<Double, Double>>()
        steps.forEachIndexed { k, st ->
            if (st.optString("activity") != "empty") return@forEachIndexed
            val a = st.getDouble("t"); val b = if (k + 1 < steps.size) steps[k + 1].getDouble("t") else s.t1
            unknownIv += a to a + EMPTY_GRACE_S
            val rest = posNear(tr, a + EMPTY_GRACE_S)
            val moved = if (rest == null) null else movedAfter(tr, rest, a + EMPTY_GRACE_S)
            val end = if (moved != null && moved < b) moved else b
            if (end > a + EMPTY_GRACE_S) emptyIv += a + EMPTY_GRACE_S to end
            if (moved == null || moved > b) unknownIv += b to minOf(moved ?: (b + RETURN_MAX_S), b + RETURN_MAX_S)
        }
        for (i in ends.indices) {
            val te = ends[i]
            if (unknownIv.any { (a, b) -> te > a && te - WIN_S < b }) continue
            if (emptyIv.any { (a, b) -> a <= te - WIN_S && te <= b }) { z[i][e] = 1.0; m[i] = 0.0; continue }
            if (tr.isEmpty()) continue
            val tp = te - POS_LAG_S
            var j = 0; var bd = Double.MAX_VALUE
            for (q in tr.indices) { val d = abs(tr[q][0] - tp); if (d < bd) { bd = d; j = q } }
            if (bd > POS_MAX_GAP_S) continue
            val room = roomAt(doubleArrayOf(tr[j][1], tr[j][2]), rooms) ?: continue
            z[i][room] = 1.0
            val seg = tr.filter { it[0] >= te - 2.0 && it[0] <= te }
            if (seg.size >= 5 && seg.last()[0] - seg.first()[0] >= 1.5) {
                var path = 0.0; for (q in 1 until seg.size) path += hypot(seg[q][1] - seg[q - 1][1], seg[q][2] - seg[q - 1][2])
                val v = path / (seg.last()[0] - seg.first()[0])
                m[i] = if (v > MOVE_YES) 1.0 else if (v < MOVE_NO) 0.0 else -1.0
            }
        }
        return z to m
    }

    private fun posNear(tr: Array<DoubleArray>, t: Double): DoubleArray? =
        tr.minByOrNull { abs(it[0] - t) }?.let { doubleArrayOf(it[1], it[2]) }

    /** First time after t0 when the phone is more than MOVED_M from where it lay: somebody picked it up. */
    private fun movedAfter(tr: Array<DoubleArray>, rest: DoubleArray, t0: Double): Double? =
        tr.firstOrNull { it[0] > t0 && hypot(it[1] - rest[0], it[2] - rest[1]) > MOVED_M }?.get(0)

    fun stats(s: Session, links: List<String>, ends: DoubleArray): Array<Array<DoubleArray?>> =
        Array(ends.size) { i -> Array(links.size) { j ->
            val fr = s.frames[links[j]]
            if (fr == null) null else Csi.linkStats(fr.first, fr.second, ends[i] - WIN_S, ends[i])
        } }

    private fun percentile(xs: List<Double>, q: Double): Double {  // numpy linear interpolation
        val s = xs.sorted(); if (s.size == 1) return s[0]
        val pos = q / 100.0 * (s.size - 1); val lo = pos.toInt(); val hi = minOf(lo + 1, s.size - 1)
        return s[lo] + (s[hi] - s[lo]) * (pos - lo)
    }

    /** Level per link: median of "nobody home" windows when there are enough of them, else the 10th percentile. */
    fun quietLevels(sx: Array<Array<DoubleArray?>>, quiet: BooleanArray, use: BooleanArray): Array<DoubleArray?> {
        val l = if (sx.isEmpty()) 0 else sx[0].size
        return Array(l) { j ->
            val alive = sx.indices.filter { use[it] && sx[it][j] != null }
            val q = alive.filter { quiet[it] }
            when {
                q.size >= QUIET_MIN -> DoubleArray(ZoneFeatures.STATS) { f -> percentile(q.map { sx[it][j]!![f] }, 50.0) }
                alive.size >= 10 -> DoubleArray(ZoneFeatures.STATS) { f -> percentile(alive.map { sx[it][j]!![f] }, FLOOR_Q) }
                else -> null
            }
        }
    }

    fun features(sx: Array<Array<DoubleArray?>>, levels: Array<DoubleArray?>): Array<Array<DoubleArray>> =
        Array(sx.size) { i -> Array(levels.size) { j -> ZoneFeatures.linkFeatures(sx[i][j], levels[j]) } }

    /** Contiguous time blocks (by window end time); train windows within WIN_S of the held-out block are dropped. */
    fun cvFolds(ends: DoubleArray, k: Int): List<Pair<BooleanArray, BooleanArray>> {
        val n = ends.size
        val fold = IntArray(n) { minOf(it * k / max(n, 1), k - 1) }
        return (0 until k).map { f ->
            val test = BooleanArray(n) { fold[it] == f }
            val idx = (0 until n).filter { test[it] }
            val train = BooleanArray(n) { !test[it] }
            if (idx.isNotEmpty()) {
                val lo = ends[idx.first()] - WIN_S; val hi = ends[idx.last()] + WIN_S
                for (i in 0 until n) if (ends[i] in lo..hi) train[i] = false
            }
            train to test
        }
    }

    // ---------------- fingerprints: where the person was ----------------
    /** Phone position at time t (the walker carries it), if the trajectory has a pose close enough. */
    fun posAt(tr: Array<DoubleArray>, t: Double): DoubleArray? {
        if (tr.isEmpty()) return null
        var lo = 0; var hi = tr.size - 1
        while (lo < hi) { val mid = (lo + hi) / 2; if (tr[mid][0] < t) lo = mid + 1 else hi = mid }
        var best = lo
        if (lo > 0 && abs(tr[lo - 1][0] - t) < abs(tr[lo][0] - t)) best = lo - 1
        return if (abs(tr[best][0] - t) <= POS_MAX_GAP_S) doubleArrayOf(tr[best][1], tr[best][2]) else null
    }

    /** Windows with a known room and a known phone position -> (embedding, position) pairs kept in the model,
     *  plus the honest median error: each window is placed without the fingerprints of the 30 s around it. */
    fun fingerprints(s: Session, ends: DoubleArray, ki: List<Int>, z: Array<DoubleArray>, xk: Array<Array<DoubleArray>>,
                     model: ZoneModel, nRooms: Int): JSONObject? {
        val idx = ArrayList<Int>(); val pos = ArrayList<DoubleArray>(); val tt = ArrayList<Double>()
        ki.forEachIndexed { q, w ->
            if (z[w].sum() != 1.0 || z[w][nRooms] == 1.0) return@forEachIndexed     // только «человек в комнате»
            val p = posAt(s.traj, ends[w] - POS_LAG_S) ?: return@forEachIndexed
            idx.add(q); pos.add(p); tt.add(ends[w])
        }
        if (idx.size < 30) return null
        val step = maxOf(1, (idx.size + FP_MAX - 1) / FP_MAX)
        val keep = idx.indices.filter { it % step == 0 }
        val e = model.embed(Array(keep.size) { xk[idx[keep[it]]] })
        val p = Array(keep.size) { pos[keep[it]] }
        val t = DoubleArray(keep.size) { tt[keep[it]] }
        val errs = ArrayList<Double>()
        for (i in keep.indices) {
            val f = ZoneModel.nearest(e[i], e, p, FP_K) { j -> abs(t[j] - t[i]) < FP_EXCLUDE_S } ?: continue
            errs.add(hypot(f.x - p[i][0], f.y - p[i][1]))
        }
        if (errs.isEmpty()) return null
        fun r(v: Double) = Math.round(v * 1000.0) / 1000.0
        return JSONObject().put("k", FP_K).put("err", r(percentile(errs, 50.0))).put("err75", r(percentile(errs, 75.0)))
            .put("e", JSONArray().also { a -> for (row in e) a.put(JSONArray().also { x -> for (v in row) x.put(r(v)) }) })
            .put("p", JSONArray().also { a -> for (row in p) a.put(JSONArray().put(r(row[0])).put(r(row[1]))) })
    }

    // ---------------- nets ----------------
    class JavaRng(seed: Long) : Trainer.TrainRng {
        val r = Random(seed)
        override fun shuffle(a: IntArray) { for (i in a.size - 1 downTo 1) { val j = r.nextInt(i + 1); val t = a[i]; a[i] = a[j]; a[j] = t } }
        override fun dropout(rows: Int, links: Int, p: Double) = Array(rows) { BooleanArray(links) { r.nextDouble() < p } }
        override fun noise(rows: Int, cols: Int, sd: Double) = Array(rows) { DoubleArray(cols) { r.nextGaussian() * sd } }
    }

    fun initNet(d: Int, k: Int, cfg: Trainer.FitConfig, r: Random): Trainer.Net {
        val (h1, h2) = cfg.hidden
        fun g(rows: Int, cols: Int, sd: Double) = Array(rows) { DoubleArray(cols) { r.nextGaussian() * sd } }
        return Trainer.Net(g(d, h1, sqrt(2.0 / d)), DoubleArray(h1), g(h1, h2, sqrt(2.0 / h1)), DoubleArray(h2),
            g(h2, k, sqrt(1.0 / h2)), DoubleArray(k), g(h2, 1, sqrt(1.0 / h2)), DoubleArray(1))
    }

    private fun netJson(n: Trainer.Net): JSONObject {
        fun mat(a: Array<DoubleArray>) = JSONArray().also { o -> for (r in a) o.put(JSONArray().also { x -> for (v in r) x.put(v) }) }
        fun vec(a: DoubleArray) = JSONArray().also { o -> for (v in a) o.put(v) }
        return JSONObject().put("W1", mat(n.w1)).put("b1", vec(n.b1)).put("W2", mat(n.w2)).put("b2", vec(n.b2))
            .put("Wz", mat(n.wz)).put("bz", vec(n.bz)).put("Wm", mat(n.wm)).put("bm", vec(n.bm))
    }

    private fun modelJson(version: Int, links: List<String>, zones: List<String>, mu: Array<DoubleArray>,
                          sd: Array<DoubleArray>, nets: List<Trainer.Net>): JSONObject {
        fun mat(a: Array<DoubleArray>) = JSONArray().also { o -> for (r in a) o.put(JSONArray().also { x -> for (v in r) x.put(v) }) }
        return JSONObject().put("version", version).put("links", JSONArray(links)).put("zones", JSONArray(zones))
            .put("mu", mat(mu)).put("sd", mat(sd)).put("nets", JSONArray().also { a -> nets.forEach { a.put(netJson(it)) } })
    }

    /** Held-out quality: per-room recall (exact labels), nobody-home and moving balanced accuracies. */
    fun metrics(z: Array<DoubleArray>, m: DoubleArray, pz: Array<DoubleArray>, pm: DoubleArray, zones: List<String>): JSONObject {
        val e = zones.size - 1
        val out = JSONObject().put("windows", z.count { it.sum() > 0 })
        val single = z.indices.filter { z[it].sum() == 1.0 }
        val y = single.associateWith { i -> z[i].indexOfFirst { it == 1.0 } }
        val yh = single.associateWith { i -> pz[i].indices.maxByOrNull { pz[i][it] }!! }
        val per = JSONObject(); val roomRec = ArrayList<Double>()
        for (k in zones.indices) {
            val ii = single.filter { y[it] == k }
            if (ii.size < 5) continue
            val rec = ii.count { yh[it] == k }.toDouble() / ii.size
            per.put(zones[k], JSONObject().put("n", ii.size).put("recall", rec))
            if (k != e) roomRec.add(rec)
        }
        out.put("zones", per)
        if (roomRec.isNotEmpty()) { out.put("zone_bal_acc", roomRec.average()); out.put("chance", 1.0 / roomRec.size) }
        val empty = z.indices.filter { z[it][e] == 1.0 }
        val present = z.indices.filter { z[it].sum() > 0 && z[it][e] == 0.0 }
        if (empty.size >= 5 && present.size >= 5)
            out.put("nobody_home_bal_acc", 0.5 * (empty.count { pz[it][e] > 0.5 }.toDouble() / empty.size +
                present.count { pz[it][e] <= 0.5 }.toDouble() / present.size))
        val yes = m.indices.filter { m[it] == 1.0 }; val no = m.indices.filter { m[it] == 0.0 }
        if (yes.size >= 5 && no.size >= 5)
            out.put("moving_bal_acc", 0.5 * (yes.count { pm[it] > 0.5 }.toDouble() / yes.size + no.count { pm[it] <= 0.5 }.toDouble() / no.size))
        return out
    }

    private fun predict(model: ZoneModel, x: Array<Array<DoubleArray>>) = model.predict(x)

    data class Result(val modelJson: String, val metrics: JSONObject, val roomsOk: Boolean, val rooms: List<Room>,
                      val links: List<String>, val message: String)

    /** Train on one walk. nets: ensemble size; folds: blocked-time check. progress(stage text, 0..1). */
    fun train(s: Session, nets: Int = 4, folds: Int = 5, cfg: Trainer.FitConfig = Trainer.FitConfig(), seed: Long = 1,
              progress: (String, Double) -> Unit = { _, _ -> }): Result {
        val rooms = rooms(s.events)
        if (rooms.isEmpty()) throw TrainError("в обходе нет комнат: отметьте углы хотя бы одной комнаты")
        val ends = windowEnds(s)
        if (ends.size < MIN_WINDOWS) throw TrainError("запись короткая (${ends.size} с): пройдите сценарий целиком")
        progress("линии", 0.05)
        val seen = s.frames.keys.filter { !it.endsWith("|") }.sorted()
        val all = stats(s, seen, ends)
        val links = seen.filterIndexed { j, _ -> all.count { it[j] != null }.toDouble() / ends.size >= LINK_MIN_SHARE }
        if (links.isEmpty()) throw TrainError("в записи нет живых линий: платы не передавали данные во время обхода")
        val sx = Array(ends.size) { i -> Array(links.size) { j -> all[i][seen.indexOf(links[j])] } }
        val zones = rooms.map { it.name } + EMPTY
        val (z, m) = labels(s, rooms, ends)
        val ki = ends.indices.filter { z[it].sum() > 0 || m[it] >= 0 }
        val roomWindows = ki.count { z[it].sum() == 1.0 && z[it][rooms.size] == 0.0 }
        if (roomWindows < MIN_WINDOWS / 2) throw TrainError("почти нет окон с известной комнатой: ходите с телефоном по комнатам")
        if (ki.size < MIN_WINDOWS) throw TrainError("мало размеченных окон (${ki.size}): пройдите сценарий подольше")
        val quiet = BooleanArray(ends.size) { z[it][rooms.size] == 1.0 }
        val zk = Array(ki.size) { z[ki[it]] }; val mk = DoubleArray(ki.size) { m[ki[it]] }
        val d = links.size * ZoneFeatures.NF; val k = zones.size
        val r = Random(seed)
        // честная проверка: блок времени предсказывает сеть, которая его не видела; тихие уровни — тоже без него
        val pz = Array(ki.size) { DoubleArray(k) }; val pm = DoubleArray(ki.size)
        val fs = cvFolds(DoubleArray(ki.size) { ends[ki[it]] }, folds)
        for ((f, fold) in fs.withIndex()) {
            progress("проверка ${f + 1}/$folds", 0.1 + 0.5 * f / folds)
            val (tr, te) = fold
            val excl = BooleanArray(ends.size); ki.forEachIndexed { i, w -> if (te[i]) excl[w] = true }
            val levels = quietLevels(sx, quiet, BooleanArray(ends.size) { !excl[it] })
            val x = features(sx, levels)
            val xk = Array(ki.size) { x[ki[it]] }
            val trI = ki.indices.filter { tr[it] }; val teI = ki.indices.filter { te[it] }
            if (trI.size < 20 || teI.isEmpty()) continue
            val xtr = Array(trI.size) { xk[trI[it]] }
            val (mu, sd) = Trainer.standardisation(xtr)
            val net = Trainer.fitNet(xtr, Array(trI.size) { zk[trI[it]] }, DoubleArray(trI.size) { mk[trI[it]] },
                mu, sd, initNet(d, k, cfg, r), cfg, JavaRng(r.nextLong()))
            val fm = ZoneModel.fromJson(modelJson(0, links, zones, mu, sd, listOf(net)).toString())
            val (p1, p2) = predict(fm, Array(teI.size) { xk[teI[it]] })
            teI.forEachIndexed { q, i -> pz[i] = p1[q]; pm[i] = p2[q] }
        }
        val cv = metrics(zk, mk, pz, pm, zones)
        progress("обучение", 0.65)
        val levels = quietLevels(sx, quiet, BooleanArray(ends.size) { true })
        val xall = features(sx, levels)
        val xk = Array(ki.size) { xall[ki[it]] }
        val (mu, sd) = Trainer.standardisation(xk)
        val ens = (0 until nets).map { n ->
            progress("обучение ${n + 1}/$nets", 0.65 + 0.33 * n / nets)
            Trainer.fitNet(xk, zk, mk, mu, sd, initNet(d, k, cfg, r), cfg, JavaRng(r.nextLong()))
        }
        progress("точки на плане", 0.98)
        val fp = fingerprints(s, ends, ki, z, xk, ZoneModel.fromJson(modelJson(0, links, zones, mu, sd, ens).toString()), rooms.size)
        val acc = cv.optDouble("zone_bal_acc", 0.0); val chance = cv.optDouble("chance", 1.0)
        val roomsOk = rooms.size >= 2 && acc >= ROOMS_MIN_ACC && acc >= chance + ROOMS_OVER_CHANCE
        val version = (System.currentTimeMillis() / 1000).toInt()
        val json = modelJson(version, links, zones, mu, sd, ens)
        val q = JSONObject(); links.forEachIndexed { j, lk -> levels[j]?.let { q.put(lk, JSONArray(it.toList())) } }
        val planJson = JSONArray(); rooms.forEach { rm ->
            planJson.put(JSONObject().put("name", rm.name).put("polygon", JSONArray(rm.poly.map { JSONArray(it.toList()) })))
        }
        val boards = JSONObject(); s.boardMarks.forEach { (mac, xy) -> boards.put(mac, JSONArray(xy.toList())) }
        json.put("quiet", q).put("rooms_ok", roomsOk).put("rooms", planJson).put("boards", boards)
            .put("session", s.id).put("windows", ki.size).put("metrics", JSONObject().put("heldout", cv))
        if (fp != null) { json.put("fp", fp); cv.put("pos_median_m", fp.getDouble("err")) }
        progress("готово", 1.0)
        val msg = if (roomsOk) "комнаты различаются: ${(acc * 100).toInt()}% (случайно ${(chance * 100).toInt()}%)"
                  else if (rooms.size < 2) "одна комната на плане: показываю квартиру целиком"
                  else "комнаты пока не различаются (${(acc * 100).toInt()}%, случайно ${(chance * 100).toInt()}%): " +
                       "показываю квартиру целиком; добавьте вторую плату"
        return Result(json.toString(), cv, roomsOk, rooms, links, msg)
    }
}
