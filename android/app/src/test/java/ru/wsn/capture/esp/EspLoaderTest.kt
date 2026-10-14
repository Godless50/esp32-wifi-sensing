package ru.wsn.capture.esp

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.security.MessageDigest

/** Drives EspLoader against a fake ROM that speaks the download-loader protocol over the SerialPort interface,
 *  so the framing, checksum, eFuse decode, image choice and MD5-verify logic are exercised without hardware. */
class EspLoaderTest {
    /** Minimal ESP32-S3 ROM emulator: consumes SLIP command frames, enqueues SLIP responses. */
    private class FakeRom : SerialPort {
        private val outbox = ByteArrayOutputStream()
        private val inDec = Slip.Decoder()
        private val flash = HashMap<Long, Byte>()
        private val regs = HashMap<Long, Long>()
        var tamper = false

        init {
            // eFuse BLOCK1: MAC 7c:4f:ad:bc:9b:54, flash cap 3 (16 MB), psram cap 1 (8 MB octal)
            regs[0x60007044L] = 0xadbc9b54L         // w0
            regs[0x60007048L] = 0x00007c4fL         // w1
            regs[0x60007044L + 12] = 0x18000000L    // w3: 3<<27
            regs[0x60007044L + 16] = 0x08L          // w4: psram low bits = 1
            regs[0x60007044L + 20] = 0x00L          // w5
        }

        private fun le(v: Long, n: Int) = ByteArray(n) { ((v shr (8 * it)) and 0xFF).toByte() }

        private fun respond(op: Int, value: Long, payload: ByteArray) {
            val b = ByteArrayOutputStream()
            val data = payload + byteArrayOf(0, 0) // 2 trailing status bytes (status, err)
            b.write(0x01); b.write(op)
            b.write(le(data.size.toLong(), 2)); b.write(le(value, 4)); b.write(data)
            outbox.write(Slip.encode(b.toByteArray()))
        }

        private fun u32(d: ByteArray, o: Int) = (0 until 4).fold(0L) { a, i -> a or ((d[o + i].toLong() and 0xFF) shl (8 * i)) }

        private fun handle(frame: ByteArray) {
            if (frame.size < 8) return
            val op = frame[1].toInt() and 0xFF
            val size = (frame[2].toInt() and 0xFF) or ((frame[3].toInt() and 0xFF) shl 8)
            val data = frame.copyOfRange(8, 8 + size)
            when (op) {
                EspLoader.SYNC -> respond(op, 0, ByteArray(0))
                EspLoader.READ_REG -> respond(op, regs[u32(data, 0)] ?: 0, ByteArray(0))
                EspLoader.WRITE_REG -> { regs[u32(data, 0)] = u32(data, 4); respond(op, 0, ByteArray(0)) }
                EspLoader.GET_SECURITY_INFO -> {
                    val p = ByteArray(20); p[12] = EspLoader.CHIP_ID_S3.toByte(); respond(op, 0, p)
                }
                EspLoader.SPI_ATTACH, EspLoader.SPI_SET_PARAMS, EspLoader.FLASH_BEGIN, EspLoader.FLASH_END ->
                    respond(op, 0, ByteArray(0))
                EspLoader.FLASH_DATA -> {
                    val len = u32(data, 0).toInt(); val seq = u32(data, 4).toInt()
                    val payload = data.copyOfRange(16, 16 + len)
                    var c = 0xEFL; for (x in payload) c = c xor (x.toLong() and 0xFF)
                    val base = flashBase + (seq.toLong() * EspLoader.FLASH_BLOCK)
                    for (i in payload.indices) flash[base + i] = payload[i]
                    respond(op, 0, ByteArray(0))
                }
                EspLoader.SPI_FLASH_MD5 -> {
                    val addr = u32(data, 0); val sz = u32(data, 4).toInt()
                    val region = ByteArray(sz) { (flash[addr + it] ?: 0xFF.toByte()) }
                    if (tamper && region.isNotEmpty()) region[0] = (region[0] + 1).toByte()
                    val hex = MessageDigest.getInstance("MD5").digest(region).joinToString("") { "%02x".format(it.toInt() and 0xFF) }
                    respond(op, 0, hex.toByteArray(Charsets.US_ASCII))
                }
            }
        }

        private var flashBase = 0L
        override fun write(data: ByteArray) {
            for (b in data) {
                val f = inDec.feed(b) ?: continue
                if ((f[1].toInt() and 0xFF) == EspLoader.FLASH_BEGIN) flashBase = u32(f.copyOfRange(8, f.size), 12)
                handle(f)
            }
        }

        override fun read(buf: ByteArray, timeoutMs: Int): Int {
            val cur = outbox.toByteArray()
            if (cur.isEmpty()) return 0
            val n = minOf(buf.size, cur.size)
            System.arraycopy(cur, 0, buf, 0, n)
            outbox.reset(); outbox.write(cur, n, cur.size - n)
            return n
        }

        override fun setLines(dtr: Boolean, rts: Boolean) {}
        override fun close() {}
    }

    @Test fun connectSyncs() {
        EspLoader(FakeRom()).connect()  // throws on failure
    }

    @Test fun readsChipInfo() {
        val info = EspLoader(FakeRom()).chipInfo()
        assertEquals(EspLoader.CHIP_ID_S3, info.chipId)
        assertEquals("7c:4f:ad:bc:9b:54", info.mac)
        assertEquals(16, info.flashMb)
        assertTrue(info.psramOctal)
    }

    @Test fun choosesMainImageForN16R8() {
        val l = EspLoader(FakeRom())
        assertEquals("main", l.chooseImage(l.chipInfo()))
    }

    @Test fun flashesAndVerifies() {
        val l = EspLoader(FakeRom())
        val img = ByteArray(2500) { ((it * 7 + 3) and 0xFF).toByte() }
        l.flashImage(0xC00000L, img)  // throws if the MD5 the ROM reports does not match
    }

    @Test fun detectsCorruptFlash() {
        val rom = FakeRom(); rom.tamper = true
        val l = EspLoader(rom)
        try { l.flashImage(0xC00000L, ByteArray(1500) { it.toByte() }); throw AssertionError("should fail") }
        catch (e: EspLoader.EspError) {}
    }
}
