package ru.wsn.capture.esp

/** A byte stream to the board (USB CDC-ACM on the phone, a tty on a computer in tests). */
interface SerialPort {
    fun write(data: ByteArray)

    /** Reads up to buf.size bytes, waiting at most timeoutMs; returns the count (0 on timeout). */
    fun read(buf: ByteArray, timeoutMs: Int): Int

    /** Modem lines; on the ESP32-S3 USB-Serial/JTAG they drive reset (RTS) and the boot strap (DTR). */
    fun setLines(dtr: Boolean, rts: Boolean)

    fun close()
}

/** Discard whatever is waiting on the port (boot log, extra replies). */
fun SerialPort.drain(quietMs: Int = 50, maxMs: Int = 500) {
    val buf = ByteArray(4096)
    val end = System.currentTimeMillis() + maxMs
    while (System.currentTimeMillis() < end && read(buf, quietMs) > 0) { /* отбросить */ }
}

/** SLIP framing of the ESP ROM loader protocol. */
object Slip {
    private const val END = 0xC0
    private const val ESC = 0xDB
    private const val ESC_END = 0xDC
    private const val ESC_ESC = 0xDD

    fun encode(payload: ByteArray): ByteArray {
        val out = java.io.ByteArrayOutputStream(payload.size + 8)
        out.write(END)
        for (b in payload) {
            when (b.toInt() and 0xFF) {
                END -> { out.write(ESC); out.write(ESC_END) }
                ESC -> { out.write(ESC); out.write(ESC_ESC) }
                else -> out.write(b.toInt())
            }
        }
        out.write(END)
        return out.toByteArray()
    }

    /** Stream decoder: feed bytes, get complete frames. */
    class Decoder {
        private val cur = java.io.ByteArrayOutputStream()
        private var inFrame = false
        private var esc = false

        fun feed(b: Byte): ByteArray? {
            val v = b.toInt() and 0xFF
            if (!inFrame) {
                if (v == END) { inFrame = true; cur.reset() }
                return null
            }
            if (esc) {
                esc = false
                cur.write(when (v) { ESC_END -> END; ESC_ESC -> ESC; else -> v })
                return null
            }
            return when (v) {
                ESC -> { esc = true; null }
                END -> if (cur.size() == 0) null else {  // пустая рамка: это начало следующей
                    val f = cur.toByteArray(); cur.reset(); f
                }
                else -> { cur.write(v); null }
            }
        }
    }
}
