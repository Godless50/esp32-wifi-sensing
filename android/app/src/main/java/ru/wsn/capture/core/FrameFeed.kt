package ru.wsn.capture.core

import java.util.ArrayDeque

/** CSI frames of a packet -> (link, capture time on the phone clock, amplitude). Mirrors live.FrameFeed:
 *  board clock -> phone clock is the minimum of (arrival - frame time) over a window (a burst that arrived late
 *  does not shift the placement); HT-LTF per link, L-LTF only for a transmitter that sent no HT frame for a
 *  while (11a/g devices). On the phone the hub's own wall clock is the arrival time. */
class FrameFeed {
    companion object {
        const val CLOCK_WINDOW_S = 30.0
        const val LEGACY_AFTER_S = 10.0
    }

    data class Frame(val src: String, val tSec: Double, val kind: String, val amp: DoubleArray, val changed: Boolean)

    private val clock = HashMap<String, ArrayDeque<Pair<Double, Double>>>()  // dev -> deque(arrival, arrival-frameTime)
    private val clockBoot = HashMap<String, Long>()
    private val kindOf = HashMap<String, String>()   // dev|src -> last LTF kind
    private val lastHt = HashMap<String, Double>()

    /** Phone time of board-clock 0 for this boot, or null if the frames carry no board clock. */
    fun offset(p: Wsn1.Packet, recvT: Double): Double? {
        val lastUs = p.frames.last().tUs
        if (lastUs <= 0) return null
        var q = clock[p.deviceId]
        if (q == null || clockBoot[p.deviceId] != p.bootId) { q = ArrayDeque(); clock[p.deviceId] = q; clockBoot[p.deviceId] = p.bootId }
        val o = recvT - lastUs / 1e6
        while (q.isNotEmpty() && q.peekLast().second >= o) q.pollLast()
        q.addLast(recvT to o)
        while (q.peekFirst().first < recvT - CLOCK_WINDOW_S) q.pollFirst()
        return q.peekFirst().second
    }

    fun frames(p: Wsn1.Packet, recvT: Double): List<Frame> {
        if (p.frames.isEmpty()) return emptyList()
        val off = offset(p, recvT)
        val lastUs = p.frames.last().tUs
        val out = ArrayList<Frame>()
        for (f in p.frames) {
            val key = "${p.deviceId}|${f.src}"
            val kind: String
            if (f.data.size >= 256) { kind = "ht"; lastHt[key] = recvT }
            else if (recvT - (lastHt[key] ?: -1e9) > LEGACY_AFTER_S) kind = "lltf"
            else continue
            val changed = (kindOf[key] ?: "ht") != kind
            kindOf[key] = kind
            val amp = Csi.ltfAmplitude(f.data, kind) ?: continue
            val tSec = if (off != null) minOf(recvT, f.tUs / 1e6 + off)
            else { val lag = (lastUs - f.tUs) / 1e6; if (lag in 0.0..2.0) recvT - lag else recvT }
            out.add(Frame(f.src, tSec, kind, amp, changed))
        }
        return out
    }
}
