package ru.wsn.capture.esp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.security.MessageDigest

/** Ties EspLoader + Console + OtgProvision together on the JVM: a fake ROM flashes and verifies, then a fake app
 *  REPL answers the provisioning commands. No phone or board needed. */
class OtgProvisionTest {
    private class FakeRom : SerialPort {
        private val outbox = ByteArrayOutputStream(); private val dec = Slip.Decoder()
        private val flash = HashMap<Long, Byte>(); private var base = 0L
        private val regs = mapOf(0x60007044L to 0xadbc9b54L, 0x60007048L to 0x00007c4fL,
            0x60007050L to 0x18000000L, 0x60007054L to 0x08L, 0x60007058L to 0L)
        private fun le(v: Long, n: Int) = ByteArray(n) { ((v shr (8 * it)) and 0xFF).toByte() }
        private fun u32(d: ByteArray, o: Int) = (0 until 4).fold(0L) { a, i -> a or ((d[o + i].toLong() and 0xFF) shl (8 * i)) }
        private fun respond(op: Int, value: Long, payload: ByteArray) {
            val data = payload + byteArrayOf(0, 0, 0x5A, 0x98.toByte())
            val b = ByteArrayOutputStream(); b.write(0x01); b.write(op); b.write(le(data.size.toLong(), 2)); b.write(le(value, 4)); b.write(data)
            outbox.write(Slip.encode(b.toByteArray()))
        }
        private fun handle(f: ByteArray) {
            val op = f[1].toInt() and 0xFF; val size = (f[2].toInt() and 0xFF) or ((f[3].toInt() and 0xFF) shl 8)
            val d = f.copyOfRange(8, 8 + size)
            when (op) {
                EspLoader.SYNC, EspLoader.SPI_ATTACH, EspLoader.SPI_SET_PARAMS, EspLoader.FLASH_END,
                EspLoader.WRITE_REG -> respond(op, 0, ByteArray(0))
                EspLoader.READ_REG -> respond(op, regs[u32(d, 0)] ?: 0, ByteArray(0))
                EspLoader.GET_SECURITY_INFO -> ByteArray(20).also { it[12] = 9 }.let { respond(op, 0, it) }
                EspLoader.FLASH_BEGIN -> { base = u32(d, 12); respond(op, 0, ByteArray(0)) }
                EspLoader.FLASH_DATA -> { val len = u32(d, 0).toInt(); val seq = u32(d, 4).toInt()
                    val p = d.copyOfRange(16, 16 + len); for (i in p.indices) flash[base + seq.toLong() * 0x400 + i] = p[i]; respond(op, 0, ByteArray(0)) }
                EspLoader.SPI_FLASH_MD5 -> { val a = u32(d, 0); val s = u32(d, 4).toInt()
                    val r = ByteArray(s) { flash[a + it] ?: 0xFF.toByte() }
                    respond(op, 0, MessageDigest.getInstance("MD5").digest(r).joinToString("") { "%02x".format(it.toInt() and 0xFF) }.toByteArray()) }
            }
        }
        override fun write(data: ByteArray) { for (b in data) dec.feed(b)?.let { if (it.size >= 8) handle(it) } }
        override fun read(buf: ByteArray, timeoutMs: Int): Int {
            val c = outbox.toByteArray(); if (c.isEmpty()) return 0
            val n = minOf(buf.size, c.size); System.arraycopy(c, 0, buf, 0, n); outbox.reset(); outbox.write(c, n, c.size - n); return n
        }
        override fun setLines(dtr: Boolean, rts: Boolean) {}
        override fun close() {}
    }

    private class FakeConsole : SerialPort {
        private val outbox = ByteArrayOutputStream(); private val line = StringBuilder()
        var wifiArgs: String? = null
        override fun write(data: ByteArray) {
            line.append(String(data, Charsets.US_ASCII))
            var nl = line.indexOf("\n")
            while (nl >= 0) {
                val cmd = line.substring(0, nl).trim(); line.delete(0, nl + 1)
                val reply = when {
                    cmd == "status" -> "STATUS wifi=IDLE mac=7c:4f:ad:bc:9b:54 server=-:5005 links=0"
                    cmd.startsWith("key set") -> "OK key (reboot to apply)"
                    cmd.startsWith("wifi set") -> { wifiArgs = cmd; "OK wifi (reboot to apply)" }
                    cmd.startsWith("server set") -> "OK server (reboot to apply)"
                    cmd == "reboot" -> "OK reboot"
                    else -> "ERR"
                }
                outbox.write((reply + "\n").toByteArray(Charsets.US_ASCII))
                nl = line.indexOf("\n")
            }
        }
        override fun read(buf: ByteArray, timeoutMs: Int): Int {
            val c = outbox.toByteArray(); if (c.isEmpty()) return 0
            val n = minOf(buf.size, c.size); System.arraycopy(c, 0, buf, 0, n); outbox.reset(); outbox.write(c, n, c.size - n); return n
        }
        override fun setLines(dtr: Boolean, rts: Boolean) {}
        override fun close() {}
    }

    @Test fun flashesThenProvisions() {
        val console = FakeConsole()
        var call = 0
        val ports = listOf<SerialPort>(FakeRom(), console)
        val prov = OtgProvision(openPort = { ports[call++] },
            images = mapOf("main" to ByteArray(3000) { (it and 0xFF).toByte() }))
        val r = prov.run("00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff",
            OtgProvision.Settings("MyWiFi", "secretpass", "98:b3:ef:02:da:28", "192.168.4.77", 5005))
        assertEquals("7c:4f:ad:bc:9b:54", r.mac)
        assertEquals("main", r.image)
        assertEquals(2, call)  // ROM port then app-console port
        assertTrue("password reached the board over the cable", console.wifiArgs!!.contains("secretpass"))
    }
}
