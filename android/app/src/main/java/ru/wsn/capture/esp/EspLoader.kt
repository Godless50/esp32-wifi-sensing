package ru.wsn.capture.esp

import java.security.MessageDigest
import ru.wsn.capture.core.Wsn1

/** ESP32-S3 ROM serial bootloader over USB-Serial/JTAG — enough to flash a merged image and verify it.
 *  Reimplemented from the documented protocol (not copied from esptool). The board is put into the download
 *  ROM by the USB-JTAG reset strap, then commands go as SLIP frames (see Slip). The phone drives this over OTG;
 *  the SerialPort is the only platform dependency, so the whole loader is testable on the JVM with a fake ROM. */
class EspLoader(private val port: SerialPort) {
    class EspError(msg: String) : Exception(msg)

    companion object {
        const val SYNC = 0x08; const val WRITE_REG = 0x09; const val READ_REG = 0x0A
        const val FLASH_BEGIN = 0x02; const val FLASH_DATA = 0x03; const val FLASH_END = 0x04
        const val SPI_ATTACH = 0x0D; const val SPI_SET_PARAMS = 0x0B; const val SPI_FLASH_MD5 = 0x13
        const val GET_SECURITY_INFO = 0x14
        const val FLASH_BLOCK = 0x400
        const val CHIP_ID_S3 = 9
        const val EFUSE_BLK1 = 0x60007044L
        const val UARTDEV_BUF_NO = 0x3FCEF14CL; const val UARTDEV_USB_JTAG = 4L
        const val RTC_WDT_CONFIG0 = 0x60008098L; const val RTC_WDT_CONFIG1 = 0x6000809CL
        const val RTC_WDT_WPROTECT = 0x600080B0L; const val WDT_WKEY = 0x50D83AA1L
        const val SWD_CONF = 0x600080B4L; const val SWD_WPROTECT = 0x600080B8L; const val SWD_WKEY = 0x8F1D312AL
        const val SPI_CMD = 0x60002000L; const val SPI_USR = 0x60002018L; const val SPI_USR2 = 0x60002020L
        const val SPI_MISO_DLEN = 0x60002028L; const val SPI_W0 = 0x60002058L
    }

    private val dec = Slip.Decoder()

    private fun send(op: Int, data: ByteArray, chk: Long = 0) {
        val body = java.io.ByteArrayOutputStream()
        body.write(0x00); body.write(op)
        body.write(data.size and 0xFF); body.write((data.size shr 8) and 0xFF)
        for (i in 0 until 4) body.write(((chk shr (8 * i)) and 0xFF).toInt())
        body.write(data)
        port.write(Slip.encode(body.toByteArray()))
    }

    private class Resp(val op: Int, val value: Long, val data: ByteArray)

    private fun readFrame(timeoutMs: Int): ByteArray? {
        val buf = ByteArray(1)
        val end = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < end) {
            val n = port.read(buf, (end - System.currentTimeMillis()).toInt().coerceIn(1, timeoutMs))
            if (n <= 0) continue
            val f = dec.feed(buf[0])
            if (f != null) return f
        }
        return null
    }

    /** Send a command and wait for its response; checks the direction/op and the status the ROM appends. */
    private fun command(op: Int, data: ByteArray = ByteArray(0), chk: Long = 0, timeoutMs: Int = 3000): Resp {
        send(op, data, chk)
        val end = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < end) {
            val f = readFrame((end - System.currentTimeMillis()).toInt().coerceAtLeast(1)) ?: break
            if (f.size < 8 || (f[0].toInt() and 0xFF) != 0x01 || (f[1].toInt() and 0xFF) != op) continue
            val size = (f[2].toInt() and 0xFF) or ((f[3].toInt() and 0xFF) shl 8)
            val value = (0 until 4).fold(0L) { a, i -> a or ((f[4 + i].toLong() and 0xFF) shl (8 * i)) }
            val payload = if (f.size >= 8 + size) f.copyOfRange(8, 8 + size) else f.copyOfRange(8, f.size)
            // ПЗУ (не stub) добавляет к данным 4 байта: статус, причина, 2 резервных (в них бывает мусор).
            if (payload.size < 4) throw EspError("command $op: short response (${payload.size} bytes)")
            val status = payload[payload.size - 4].toInt() and 0xFF
            val reason = payload[payload.size - 3].toInt() and 0xFF
            if (status != 0) throw EspError("command $op failed, reason 0x%02x".format(reason))
            return Resp(op, value, payload.copyOf(payload.size - 4))
        }
        throw EspError("no response to command $op")
    }

    private fun le32(v: Long) = ByteArray(4) { ((v shr (8 * it)) and 0xFF).toByte() }
    private fun words(vararg v: Long): ByteArray {
        val o = java.io.ByteArrayOutputStream(); for (x in v) o.write(le32(x)); return o.toByteArray()
    }

    /** USB-Serial/JTAG reset strap into the download ROM (does not need the auto-reset capacitor circuit). */
    fun resetIntoBootloader() {
        port.setLines(dtr = false, rts = false); sleep(100)
        port.setLines(dtr = true, rts = false); sleep(100)
        // RTS поднимается, пока DTR ещё поднят: так чип защёлкивает режим загрузки (как esptool). Сразу (true,false) ->
        // (false,true) порт компьютера проходит через (false,false), и чип просто перезагружается в прошивку.
        port.setLines(dtr = true, rts = true)
        port.setLines(dtr = false, rts = true); sleep(100)
        port.setLines(dtr = false, rts = false); sleep(100)
    }

    fun connect(attempts: Int = 10) {
        resetIntoBootloader()
        port.drain(quietMs = 50, maxMs = 300)
        val sync = byteArrayOf(0x07, 0x07, 0x12, 0x20) + ByteArray(32) { 0x55 }
        for (a in 0 until attempts) {
            try {
                send(SYNC, sync)
                val end = System.currentTimeMillis() + 150
                while (System.currentTimeMillis() < end) {
                    val f = readFrame(100) ?: continue
                    if (f.size >= 2 && (f[0].toInt() and 0xFF) == 0x01 && (f[1].toInt() and 0xFF) == SYNC) {
                        port.drain(quietMs = 30, maxMs = 150)  // ROM answers SYNC several times
                        return
                    }
                }
            } catch (_: Exception) {}
            sleep(100)
        }
        throw EspError("sync failed: board not in the download ROM")
    }

    fun readReg(addr: Long): Long = command(READ_REG, le32(addr)).value
    fun writeReg(addr: Long, value: Long, mask: Long = 0xFFFFFFFFL, delayUs: Long = 0) {
        command(WRITE_REG, words(addr, value, mask, delayUs))
    }

    fun chipId(): Int {
        val d = command(GET_SECURITY_INFO).data
        if (d.size < 16) throw EspError("security info too short")
        return (d[12].toInt() and 0xFF) or ((d[13].toInt() and 0xFF) shl 8) or
            ((d[14].toInt() and 0xFF) shl 16) or ((d[15].toInt() and 0xFF) shl 24)
    }

    data class ChipInfo(val chipId: Int, val mac: String, val flashMb: Int, val psramOctal: Boolean)

    /** MAC and flash/PSRAM capacity from eFuse BLOCK1 (the same words the firmware reads). */
    fun chipInfo(): ChipInfo {
        val id = chipId()
        val w0 = readReg(EFUSE_BLK1); val w1 = readReg(EFUSE_BLK1 + 4)
        val w3 = readReg(EFUSE_BLK1 + 12); val w4 = readReg(EFUSE_BLK1 + 16); val w5 = readReg(EFUSE_BLK1 + 20)
        val mac = ByteArray(6)
        val packed = byteArrayOf(((w1 shr 24) and 0xFF).toByte(), ((w1 shr 16) and 0xFF).toByte(),
            ((w1 shr 8) and 0xFF).toByte(), (w1 and 0xFF).toByte(),
            ((w0 shr 24) and 0xFF).toByte(), ((w0 shr 16) and 0xFF).toByte(),
            ((w0 shr 8) and 0xFF).toByte(), (w0 and 0xFF).toByte())
        System.arraycopy(packed, 2, mac, 0, 6)
        val flashCap = ((w3 shr 27) and 7).toInt()
        val psramCap = (((w4 shr 3) and 3) or (((w5 shr 19) and 1) shl 2)).toInt()
        val flashMb = when (flashCap) { 1 -> 8; 2 -> 4; 3 -> 16; 4 -> 8; 5 -> 32; else -> 0 }
        val psramOctal = psramCap == 1 || psramCap == 3  // 1 = 8 MB octal, 3 = 16 MB
        return ChipInfo(id, Wsn1.macStr(mac), flashMb, psramOctal)
    }

    /** The download ROM entered over USB-Serial/JTAG keeps the RTC and super watchdogs of the app armed: they reset
     *  the chip a few seconds into a flash write. Turn both off (only when the chip really is on USB-Serial/JTAG). */
    fun disableWatchdogs() {
        if ((readReg(UARTDEV_BUF_NO) and 0xFF) != UARTDEV_USB_JTAG) return
        writeReg(RTC_WDT_WPROTECT, WDT_WKEY)
        writeReg(RTC_WDT_CONFIG0, 0)
        writeReg(RTC_WDT_WPROTECT, 0)
        writeReg(SWD_WPROTECT, SWD_WKEY)
        writeReg(SWD_CONF, readReg(SWD_CONF) or (1L shl 31))  // автокормление сторожа питания
        writeReg(SWD_WPROTECT, 0)
    }

    /** Leave the ROM and boot the app: an RTC watchdog reset (a ROM "reboot" over USB-Serial/JTAG can land back in
     *  the download mode). No reply comes: the chip resets and the USB device enumerates again. */
    fun watchdogReset() {
        writeReg(RTC_WDT_WPROTECT, WDT_WKEY)
        writeReg(RTC_WDT_CONFIG1, 2000)
        try {  // сторож срабатывает через ~15 мс: ответ на эти записи может не прийти или прийти обрывком
            writeReg(RTC_WDT_CONFIG0, (1L shl 31) or (5L shl 28) or (1L shl 8) or 2)
            writeReg(RTC_WDT_WPROTECT, 0)
        } catch (_: Exception) {}
    }

    /** JEDEC id of the SPI flash (RDID 0x9F) through the SPI1 user-command registers. Module flash (WROOM N16R8)
     *  is not described in eFuse, so its size comes from here. */
    fun flashId(): Long {
        val usr = readReg(SPI_USR); val usr2 = readReg(SPI_USR2)
        writeReg(SPI_MISO_DLEN, 23)
        writeReg(SPI_USR, (1L shl 31) or (1L shl 28))         // команда + приём (MISO)
        writeReg(SPI_USR2, (7L shl 28) or 0x9F)               // 8-битная команда 0x9F
        writeReg(SPI_W0, 0)
        writeReg(SPI_CMD, 1L shl 18)
        val end = System.currentTimeMillis() + 500
        while (readReg(SPI_CMD) and (1L shl 18) != 0L) if (System.currentTimeMillis() > end) throw EspError("RDID timeout")
        val id = readReg(SPI_W0) and 0xFFFFFF
        writeReg(SPI_USR, usr); writeReg(SPI_USR2, usr2)
        return id
    }

    /** Flash size in MB: eFuse when the flash is in the package, else the JEDEC capacity byte. */
    fun flashSizeMb(info: ChipInfo): Int {
        if (info.flashMb > 0) return info.flashMb
        val cap = ((flashId() shr 16) and 0xFF).toInt()
        return if (cap in 0x14..0x1A) (1 shl (cap - 0x14)) else 0
    }

    fun spiAttach() { command(SPI_ATTACH, words(0)) }
    fun spiSetParams(sizeBytes: Long) { command(SPI_SET_PARAMS, words(0, sizeBytes, 0x10000, 0x1000, 0x100, 0xFFFF)) }

    private fun checksum(data: ByteArray): Long {
        var c = 0xEFL; for (b in data) c = c xor (b.toLong() and 0xFF); return c
    }

    /** Write a merged image at `offset`, then verify the flashed range by MD5. Throws on mismatch. */
    fun flashImage(offset: Long, image: ByteArray, onProgress: ((Int) -> Unit)? = null) {
        val blocks = (image.size + FLASH_BLOCK - 1) / FLASH_BLOCK
        val eraseMs = maxOf(3000L, 30000L * image.size / (1024 * 1024) + 3000L).toInt()  // стирание ~30 с на МБ
        command(FLASH_BEGIN, words(image.size.toLong(), blocks.toLong(), FLASH_BLOCK.toLong(), offset) + le32(0),
            timeoutMs = eraseMs)
        var seq = 0; var pos = 0
        while (pos < image.size) {
            val chunk = image.copyOfRange(pos, minOf(pos + FLASH_BLOCK, image.size))
            val padded = if (chunk.size < FLASH_BLOCK) chunk + ByteArray(FLASH_BLOCK - chunk.size) { 0xFF.toByte() } else chunk
            command(FLASH_DATA, words(padded.size.toLong(), seq.toLong(), 0, 0) + padded, checksum(padded), timeoutMs = 5000)
            pos += chunk.size; seq++
            onProgress?.invoke(pos * 100 / image.size)
        }
        val md5 = flashMd5(offset, image.size.toLong())
        val want = MessageDigest.getInstance("MD5").digest(image).joinToString("") { "%02x".format(it.toInt() and 0xFF) }
        if (md5 != want) throw EspError("flash verify failed: $md5 != $want")
    }

    /** ROM replies with 32 ASCII hex characters (the md5 of the flash range). */
    fun flashMd5(addr: Long, size: Long): String {
        val d = command(SPI_FLASH_MD5, words(addr, size, 0, 0), timeoutMs = 10000).data
        return if (d.size >= 32) String(d, 0, 32, Charsets.US_ASCII)
        else d.joinToString("") { "%02x".format(it.toInt() and 0xFF) }
    }

    fun finishFlash(reboot: Boolean) { command(FLASH_END, le32(if (reboot) 0 else 1)) }

    /** "main" (16 MB layout, octal PSRAM) needs both; anything else with >= 8 MB flash gets "lite" (8 MB layout,
     *  runs without PSRAM — an N8R8 module too). Less than 8 MB flash: not supported. */
    fun chooseImage(info: ChipInfo): String {
        if (info.flashMb < 8) throw EspError("flash ${info.flashMb} MB: at least 8 MB required")
        return if (info.psramOctal && info.flashMb >= 16) "main" else "lite"
    }

    private fun sleep(ms: Long) = try { Thread.sleep(ms) } catch (_: InterruptedException) {}
}
