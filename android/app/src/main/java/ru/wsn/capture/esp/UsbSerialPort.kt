package ru.wsn.capture.esp

import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbManager

/** SerialPort over Android USB host for the ESP32-S3 built-in USB-Serial/JTAG (CDC-ACM, VID 0x303A PID 0x1001).
 *  DTR/RTS go through the CDC SET_CONTROL_LINE_STATE control request, which on the S3 drive the download strap
 *  and reset — EspLoader uses them to enter the ROM. */
class UsbSerialPort private constructor(
    private val conn: UsbDeviceConnection,
    private val epIn: android.hardware.usb.UsbEndpoint,
    private val epOut: android.hardware.usb.UsbEndpoint,
    private val ctrlIface: Int,
) : SerialPort {

    override fun write(data: ByteArray) {
        var off = 0
        while (off < data.size) {
            val chunk = minOf(epOut.maxPacketSize * 8, data.size - off)
            val n = conn.bulkTransfer(epOut, data.copyOfRange(off, off + chunk), chunk, 2000)
            if (n < 0) throw EspLoader.EspError("usb write failed"); off += n
        }
    }

    // Всегда просим целый буфер: запрос короче пакета устройства (64 байта) usbfs обрывает переполнением, и ответ
    // теряется. Лишнее хранится до следующего чтения (загрузчик читает по одному байту).
    private val rx = ByteArray(4096)
    private var rxPos = 0
    private var rxLen = 0
    override fun read(buf: ByteArray, timeoutMs: Int): Int {
        if (rxPos >= rxLen) {
            val n = conn.bulkTransfer(epIn, rx, rx.size, timeoutMs.coerceAtLeast(1))
            if (n <= 0) return 0
            rxPos = 0; rxLen = n
        }
        val k = minOf(buf.size, rxLen - rxPos)
        System.arraycopy(rx, rxPos, buf, 0, k)
        rxPos += k
        return k
    }

    override fun setLines(dtr: Boolean, rts: Boolean) {
        val value = (if (dtr) 0x01 else 0) or (if (rts) 0x02 else 0)
        // bmRequestType 0x21 (host->device, class, interface), bRequest 0x22 SET_CONTROL_LINE_STATE
        conn.controlTransfer(0x21, 0x22, value, ctrlIface, null, 0, 2000)
    }

    override fun close() {
        try { conn.close() } catch (_: Exception) {}
    }

    companion object {
        const val ESP_VID = 0x303A
        const val ESP_JTAG_PID = 0x1001

        fun matches(d: UsbDevice): Boolean =
            d.vendorId == ESP_VID || (0 until d.interfaceCount).any { d.getInterface(it).interfaceClass == UsbConstants.USB_CLASS_CDC_DATA }

        /** Open the CDC-ACM data interface of `device`. Caller must already hold USB permission for it. */
        fun open(manager: UsbManager, device: UsbDevice): UsbSerialPort {
            val conn = manager.openDevice(device) ?: throw EspLoader.EspError("cannot open USB device")
            var dataIface: android.hardware.usb.UsbInterface? = null
            var ctrlIface = 0
            for (i in 0 until device.interfaceCount) {
                val iface = device.getInterface(i)
                if (iface.interfaceClass == UsbConstants.USB_CLASS_CDC_DATA) dataIface = iface
                if (iface.interfaceClass == UsbConstants.USB_CLASS_COMM) ctrlIface = iface.id
            }
            if (dataIface == null) {  // fall back: first interface with two bulk endpoints
                for (i in 0 until device.interfaceCount) {
                    val iface = device.getInterface(i)
                    val bulk = (0 until iface.endpointCount).map { iface.getEndpoint(it) }
                        .filter { it.type == UsbConstants.USB_ENDPOINT_XFER_BULK }
                    if (bulk.size >= 2) { dataIface = iface; break }
                }
            }
            val iface = dataIface ?: throw EspLoader.EspError("no CDC data interface")
            if (!conn.claimInterface(iface, true)) { conn.close(); throw EspLoader.EspError("claimInterface failed") }
            var epIn: android.hardware.usb.UsbEndpoint? = null
            var epOut: android.hardware.usb.UsbEndpoint? = null
            for (e in 0 until iface.endpointCount) {
                val ep = iface.getEndpoint(e)
                if (ep.type != UsbConstants.USB_ENDPOINT_XFER_BULK) continue
                if (ep.direction == UsbConstants.USB_DIR_IN) epIn = ep else epOut = ep
            }
            if (epIn == null || epOut == null) { conn.close(); throw EspLoader.EspError("no bulk endpoints") }
            return UsbSerialPort(conn, epIn, epOut, ctrlIface)
        }
    }
}
