package ru.wsn.capture.core

import java.util.ArrayDeque
import kotlin.math.exp
import kotlin.math.ln

/** The phone-as-hub logic without any Android: parse incoming datagrams, keep per-link amplitude buffers, and
 *  once a second turn the last 4 s into a room/empty/moving verdict with ZoneModel. The Android service wraps
 *  this with the real UDP socket, wake locks and a file sink for the raw recording. Testable on the JVM. */
class HubEngine(
    @Volatile var model: ZoneModel? = null,
    quiet: Map<String, DoubleArray> = emptyMap(),
    private val keyFor: (String) -> ByteArray?,
    private val rawSink: ((ByteArray, Double) -> Unit)? = null,
) {
    companion object {
        const val WIN_S = 4.0
        const val LAG_S = 0.5
        const val BUF_S = WIN_S + LAG_S + 2.0
        const val EPS = 1e-4
        const val ADAPT_EMPTY_P = 0.9
        const val ADAPT_AFTER_S = 60.0
        const val ADAPT_RATE = 1.0 / 600
    }

    private val buffers = HashMap<String, ArrayDeque<Pair<Double, DoubleArray>>>()
    private val feed = FrameFeed()
    private val quiet = HashMap<String, DoubleArray>(quiet)
    @Volatile var lastTelemetry: Wsn1.Telemetry? = null
    @Volatile var lastSurvey: Wsn1.Survey? = null
    private var emptySince: Double? = null

    fun setQuiet(levels: Map<String, DoubleArray>) { quiet.clear(); quiet.putAll(levels) }

    /** Verify (with the board's key) and ingest one datagram captured at `recvT` (seconds, the phone's clock). */
    fun onDatagram(buf: ByteArray, recvT: Double): Wsn1.Packet? {
        val dev = try { Wsn1.peekDeviceId(buf) } catch (e: Exception) { return null }
        val p = try { Wsn1.parse(buf, keyFor(dev)) } catch (e: Exception) { return null }
        when (p.type) {
            Wsn1.TYPE_CSI, Wsn1.TYPE_CSI_SRC -> {
                rawSink?.invoke(buf, recvT)
                for (f in feed.frames(p, recvT)) {
                    val key = "${p.deviceId}|${f.src}"
                    val q = buffers.getOrPut(key) { ArrayDeque() }
                    q.addLast(f.tSec to f.amp)
                    while (q.isNotEmpty() && q.peekFirst().first < f.tSec - BUF_S) q.pollFirst()
                }
            }
            Wsn1.TYPE_TELEM -> lastTelemetry = p.telemetry
            Wsn1.TYPE_SURVEY -> lastSurvey = p.survey
        }
        return p
    }

    private fun windowStats(link: String, t1: Double): DoubleArray? {
        val q = buffers[link] ?: return null
        if (q.isEmpty()) return null
        val arr = q.toList().sortedBy { it.first }
        val t = DoubleArray(arr.size) { arr[it].first }
        val a = Array(arr.size) { arr[it].second }
        return Csi.linkStats(t, a, t1 - WIN_S, t1)
    }

    /** Verdict for the window ending at `now - LAG_S`, or null if no model is loaded. */
    fun tick(now: Double): ZoneModel.Verdict? {
        val m = model ?: return null
        val t1 = now - LAG_S
        val x = Array(1) { Array(m.links.size) { j ->
            val st = windowStats(m.links[j], t1)
            ZoneFeatures.linkFeatures(st, quiet[m.links[j]])
        } }
        val v = m.verdict(x)
        if (v.emptyP >= ADAPT_EMPTY_P) {
            if (emptySince == null) emptySince = now
            if (now - emptySince!! >= ADAPT_AFTER_S) for (lk in m.links) adapt(lk, windowStats(lk, t1))
        } else emptySince = null
        return v
    }

    /** Follow slow radio drift of a link's quiet level while the flat is empty (geometric EMA), as LiveInfer. */
    private fun adapt(link: String, st: DoubleArray?) {
        if (st == null) return
        val lvl = quiet[link] ?: return
        for (i in lvl.indices) lvl[i] = exp((1 - ADAPT_RATE) * ln(lvl[i] + EPS) + ADAPT_RATE * ln(st[i] + EPS)) - EPS
    }

    fun liveLinks(now: Double, withinS: Double = 10.0): List<String> =
        buffers.filter { it.value.isNotEmpty() && it.value.peekLast().first >= now - withinS }.keys.sorted()
}
