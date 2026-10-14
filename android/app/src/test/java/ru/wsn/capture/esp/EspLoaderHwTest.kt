package ru.wsn.capture.esp

import com.fazecast.jSerialComm.SerialPort as JPort
import org.junit.Assert.assertEquals
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.security.MessageDigest

/** EspLoader on a real ESP32-S3 over its USB-Serial/JTAG (a computer stands in for the phone). Runs only with
 *  WSN_HW_PORT=/dev/ttyACMx. Non-destructive: reads the chip, writes 64 KB of a pattern to 0xC00000 (beyond the
 *  last partition of both layouts), checks it by MD5, then boots the app firmware again by a watchdog reset. */
class EspLoaderHwTest {
    private class JvmPort(path: String) : SerialPort {
        private val p = JPort.getCommPort(path).apply {
            setComPortParameters(115200, 8, JPort.ONE_STOP_BIT, JPort.NO_PARITY)
            setComPortTimeouts(JPort.TIMEOUT_READ_SEMI_BLOCKING, 100, 0)
            clearDTR(); clearRTS()
            if (!openPort()) throw IllegalStateException("cannot open $path")
        }
        override fun write(data: ByteArray) { p.writeBytes(data, data.size) }
        override fun read(buf: ByteArray, timeoutMs: Int): Int {
            p.setComPortTimeouts(JPort.TIMEOUT_READ_SEMI_BLOCKING, timeoutMs, 0)
            return maxOf(0, p.readBytes(buf, buf.size))
        }
        override fun setLines(dtr: Boolean, rts: Boolean) {
            if (dtr) p.setDTR() else p.clearDTR()
            if (rts) p.setRTS() else p.clearRTS()
        }
        override fun close() { p.closePort() }
    }

    @Test fun realBoard() {
        val path = System.getenv("WSN_HW_PORT")
        assumeTrue("WSN_HW_PORT not set", !path.isNullOrEmpty())
        val port = JvmPort(path!!)
        try {
            val l = EspLoader(port)
            l.connect()
            l.disableWatchdogs()
            l.spiAttach()
            val chip = l.chipInfo()
            val mb = l.flashSizeMb(chip)
            println("HW chip=${chip.chipId} mac=${chip.mac} efuse_flash=${chip.flashMb}MB jedec=%06x flash=${mb}MB psram_octal=${chip.psramOctal} image=${l.chooseImage(chip.copy(flashMb = mb))}".format(l.flashId()))
            assertEquals(EspLoader.CHIP_ID_S3, chip.chipId)
            l.spiSetParams(mb.toLong() * 1024 * 1024)
            val img = ByteArray(64 * 1024) { ((it * 131 + 7) xor (it shr 8)).toByte() }
            val t0 = System.currentTimeMillis()
            l.flashImage(0xC00000L, img)
            println("HW wrote+verified 64 KB in ${System.currentTimeMillis() - t0} ms, md5 " +
                MessageDigest.getInstance("MD5").digest(img).joinToString("") { "%02x".format(it.toInt() and 0xFF) })
            l.watchdogReset()
        } finally {
            port.close()
        }
    }
}
