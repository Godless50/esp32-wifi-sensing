package ru.wsn.capture.esp

/** The board's USB REPL (after it runs the app firmware): the same commands wsn.provision uses, over a
 *  SerialPort. Only the provisioning path writes the Wi-Fi password, and it goes straight into the board's NVS
 *  over the local cable — it is never logged or kept. */
class Console(private val port: SerialPort) {
    private val buf = StringBuilder()

    fun send(cmd: String) {
        port.write((cmd + "\r\n").toByteArray(Charsets.US_ASCII))
    }

    /** Collect lines for up to `ms`, yielding each complete line. */
    fun lines(ms: Int): List<String> {
        val out = ArrayList<String>()
        val tmp = ByteArray(1024)
        val end = System.currentTimeMillis() + ms
        while (System.currentTimeMillis() < end) {
            val n = port.read(tmp, (end - System.currentTimeMillis()).toInt().coerceIn(1, ms))
            if (n <= 0) continue
            buf.append(String(tmp, 0, n, Charsets.UTF_8))
            var nl = buf.indexOf("\n")
            while (nl >= 0) { out.add(buf.substring(0, nl).trim()); buf.delete(0, nl + 1); nl = buf.indexOf("\n") }
        }
        return out
    }

    fun expect(needle: String, ms: Int): String? {
        val end = System.currentTimeMillis() + ms
        while (System.currentTimeMillis() < end) {
            for (l in lines(300)) if (l.contains(needle)) return l
        }
        return null
    }

    /** STATUS fields as a map, retrying because opening the USB-Serial/JTAG port can reset the chip. */
    fun status(attempts: Int = 6): Map<String, String>? {
        for (a in 0 until attempts) {
            send("status")
            for (l in lines(1000)) if (l.startsWith("STATUS ")) {
                return l.removePrefix("STATUS ").split(" ").mapNotNull {
                    val i = it.indexOf('='); if (i > 0) it.substring(0, i) to it.substring(i + 1) else null
                }.toMap()
            }
        }
        return null
    }

    data class Provision(val keyHex: String, val ssid: String, val password: String, val bssid: String,
                         val host: String, val port: Int)

    /** Key, Wi-Fi and server into NVS, then reboot. Returns the board MAC from its STATUS line. */
    fun provision(p: Provision): String {
        val st = status() ?: throw EspLoader.EspError("board does not answer on USB")
        val mac = st["mac"] ?: throw EspLoader.EspError("no MAC in STATUS")
        send("key set ${p.keyHex}")
        if (expect("OK key", 3000) == null) throw EspLoader.EspError("key set failed")
        send("wifi set ${p.ssid} ${p.password} ${p.bssid}")   // password -> NVS only; not logged
        if (expect("OK wifi", 3000) == null) throw EspLoader.EspError("wifi set failed")
        send("server set ${p.host} ${p.port}")
        if (expect("OK server", 3000) == null) throw EspLoader.EspError("server set failed")
        send("reboot")
        return mac
    }
}
