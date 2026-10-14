package ru.wsn.capture.esp

/** The board's USB REPL (after it runs the app firmware): the same commands wsn.provision uses, over a
 *  SerialPort. Only the provisioning path writes the Wi-Fi password, and it goes straight into the board's NVS
 *  over the local cable — it is never logged or kept. */
class Console(private val port: SerialPort) {
    private val buf = java.io.ByteArrayOutputStream()

    fun send(cmd: String) {
        port.write((cmd + "\r\n").toByteArray(Charsets.UTF_8))
    }

    /** One argument for the board's console (esp_console splits on spaces; quotes and backslashes escape). */
    fun quote(s: String) = "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\""

    data class Ap(val bssid: String, val channel: Int, val rssi: Int, val ssid: String)

    /** Access points the board hears ("scan": AP <bssid> ch=<n> rssi=<dBm> ssid=<name> ... AP_END). */
    fun scan(ms: Int = 12000): List<Ap> {
        send("scan")
        val out = ArrayList<Ap>()
        val re = Regex("""^AP ([0-9a-f:]{17}) ch=(\d+) rssi=(-?\d+) ssid=(.*)$""")
        val end = System.currentTimeMillis() + ms
        while (System.currentTimeMillis() < end) {
            for (l in lines(300)) {
                if (l.startsWith("AP_END")) return out
                re.find(l)?.let { m -> out.add(Ap(m.groupValues[1], m.groupValues[2].toInt(), m.groupValues[3].toInt(), m.groupValues[4])) }
            }
        }
        return out
    }

    /** The node of the home network the board hears best (mesh: each board joins its nearest node). */
    fun bestBssid(ssid: String): String? = scan().filter { it.ssid == ssid }.maxByOrNull { it.rssi }?.bssid

    /** Collect lines for up to `ms`, yielding each complete line. */
    fun lines(ms: Int): List<String> {
        val out = ArrayList<String>()
        val tmp = ByteArray(1024)
        val end = System.currentTimeMillis() + ms
        while (System.currentTimeMillis() < end) {
            val n = port.read(tmp, (end - System.currentTimeMillis()).toInt().coerceIn(1, ms))
            if (n <= 0) continue
            // байты копятся до конца строки: буква UTF-8 (имя сети по-русски) может разрезаться между пакетами USB
            for (i in 0 until n) {
                if (tmp[i] == '\n'.code.toByte()) {
                    out.add(String(buf.toByteArray(), Charsets.UTF_8).removeSuffix("\r").trimStart())
                    buf.reset()
                } else buf.write(tmp[i].toInt())
            }
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
        send("wifi set ${quote(p.ssid)} ${quote(p.password)} ${p.bssid}")   // password -> NVS only; not logged
        if (expect("OK wifi", 3000) == null) throw EspLoader.EspError("wifi set failed")
        send("server set ${p.host} ${p.port}")
        if (expect("OK server", 3000) == null) throw EspLoader.EspError("server set failed")
        send("reboot")
        return mac
    }
}
