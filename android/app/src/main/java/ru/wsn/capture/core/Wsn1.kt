package ru.wsn.capture.core

import java.nio.ByteBuffer
import java.nio.ByteOrder
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/** WSN1 UDP packet format (spec 4.3), the phone-hub side. Little-endian, HMAC-SHA256 truncated to 16 bytes.
 *  Mirrors server/wsn/protocol.py and firmware/components/wsn_proto; cross-checked by golden vectors. */
object Wsn1 {
    const val MAGIC = "WSN1"
    const val VERSION = 1
    const val HEADER_LEN = 21
    const val HMAC_LEN = 16
    const val MAX_DATAGRAM = 1400

    const val TYPE_CSI = 1
    const val TYPE_PRED = 2
    const val TYPE_TELEM = 3
    const val TYPE_CSI_SRC = 4
    const val TYPE_MODEL_CHUNK = 6
    const val TYPE_MODEL_ACK = 7
    const val TYPE_DIAG = 8
    const val TYPE_LOG = 9
    const val TYPE_HUB = 10
    const val TYPE_CMD = 11
    const val TYPE_SURVEY = 12

    const val CMD_SURVEY = 1
    const val CMD_LINKS = 2
    const val CMD_SURVEY_MAX_EXCL = 16
    const val SURVEY_MAX = 40
    const val SURVEY_F_CHOSEN = 1
    const val SURVEY_F_RANDOM_MAC = 2
    const val SURVEY_F_KNOWN = 4
    const val SURVEY_F_UNHEARD = 8
    const val MODEL_CHUNK_MAX = 1024

    class ProtocolError(msg: String) : Exception(msg)

    fun hmac16(key: ByteArray, body: ByteArray): ByteArray {
        val m = Mac.getInstance("HmacSHA256")
        m.init(SecretKeySpec(key, "HmacSHA256"))
        return m.doFinal(body).copyOf(HMAC_LEN)
    }

    fun macStr(raw: ByteArray, off: Int = 0): String =
        (0 until 6).joinToString(":") { "%02x".format(raw[off + it].toInt() and 0xFF) }

    fun macBytes(mac: String): ByteArray {
        val p = mac.split(":")
        if (p.size != 6) throw ProtocolError("bad mac $mac")
        return ByteArray(6) { p[it].toInt(16).toByte() }
    }

    fun ipBytes(ip: String): ByteArray {
        val p = ip.split(".")
        if (p.size != 4 || p.any { it.toIntOrNull()?.let { v -> v !in 0..255 } != false }) throw ProtocolError("bad ipv4 $ip")
        return ByteArray(4) { p[it].toInt().toByte() }
    }

    fun ipStr(raw: ByteArray, off: Int = 0): String =
        (0 until 4).joinToString(".") { (raw[off + it].toInt() and 0xFF).toString() }

    data class CsiFrame(val tUs: Long, val rssi: Int, val noise: Int, val sigMode: Int, val cwb: Int,
                        val data: ByteArray, val src: String = "")

    data class Telemetry(val csiHz: Float, val dropRatio: Float, val rssiAvg: Int, val heapFree: Long,
                         val psramFree: Long, val uptimeS: Long, val fwVer: Long, val modelVer: Long, val wifiState: Int)

    data class Prediction(val modelVer: Long, val inferMs: Int, val linksLive: Int,
                          val act: FloatArray, val zone: FloatArray, val move: Float)

    data class ModelAck(val version: Long, val received: Long, val state: Int, val slot: Int, val error: Int)

    data class SurveyEntry(val ip: String, val mac: String, val perPing: Float, val rssi: Int, val ht: Int, val flags: Int) {
        val chosen get() = flags and SURVEY_F_CHOSEN != 0
        val randomMac get() = flags and SURVEY_F_RANDOM_MAC != 0
        val known get() = flags and SURVEY_F_KNOWN != 0
        val unheard get() = flags and SURVEY_F_UNHEARD != 0
    }

    data class Survey(val state: Int, val progress: Int, val responders: Int, val entries: List<SurveyEntry>)

    data class Packet(
        val type: Int, val deviceId: String, val bootId: Long, val seq: Long,
        val frames: List<CsiFrame> = emptyList(), val telemetry: Telemetry? = null,
        val prediction: Prediction? = null, val modelAck: ModelAck? = null, val log: String? = null,
        val survey: Survey? = null,
    )

    private fun u32(b: ByteArray, o: Int): Long =
        (b[o].toLong() and 0xFF) or ((b[o + 1].toLong() and 0xFF) shl 8) or
            ((b[o + 2].toLong() and 0xFF) shl 16) or ((b[o + 3].toLong() and 0xFF) shl 24)

    private fun u16(b: ByteArray, o: Int): Int = (b[o].toInt() and 0xFF) or ((b[o + 1].toInt() and 0xFF) shl 8)

    fun peekDeviceId(buf: ByteArray): String {
        if (buf.size < HEADER_LEN + HMAC_LEN) throw ProtocolError("length: too short")
        if (String(buf, 0, 4, Charsets.US_ASCII) != MAGIC) throw ProtocolError("magic")
        return macStr(buf, 6)
    }

    /** Verify HMAC (when key != null) and parse. verify=false for recordings checked on arrival. */
    fun parse(buf: ByteArray, key: ByteArray?, verify: Boolean = true): Packet {
        if (buf.size < HEADER_LEN + HMAC_LEN) throw ProtocolError("length: too short")
        val bodyLen = buf.size - HMAC_LEN
        if (verify) {
            if (key == null) throw ProtocolError("hmac mismatch")
            val want = hmac16(key, buf.copyOfRange(0, bodyLen))
            var diff = 0
            for (i in 0 until HMAC_LEN) diff = diff or (want[i].toInt() xor buf[bodyLen + i].toInt())
            if (diff != 0) throw ProtocolError("hmac mismatch")
        }
        if (String(buf, 0, 4, Charsets.US_ASCII) != MAGIC) throw ProtocolError("magic")
        if ((buf[4].toInt() and 0xFF) != VERSION) throw ProtocolError("version")
        val ptype = buf[5].toInt() and 0xFF
        val dev = macStr(buf, 6)
        val bootId = u32(buf, 12)
        val seq = u32(buf, 16)
        val n = buf[20].toInt() and 0xFF
        var off = HEADER_LEN
        val frames = ArrayList<CsiFrame>()
        var telemetry: Telemetry? = null
        var prediction: Prediction? = null
        var modelAck: ModelAck? = null
        var log: String? = null
        var survey: Survey? = null
        when (ptype) {
            TYPE_CSI, TYPE_CSI_SRC -> {
                repeat(n) {
                    if (off + 14 > bodyLen) throw ProtocolError("length: frame header")
                    val tUs = ByteBuffer.wrap(buf, off, 8).order(ByteOrder.LITTLE_ENDIAN).long
                    val rssi = buf[off + 8].toInt()
                    val noise = buf[off + 9].toInt()
                    val sig = buf[off + 10].toInt() and 0xFF
                    val cwb = buf[off + 11].toInt() and 0xFF
                    val dlen = u16(buf, off + 12)
                    off += 14
                    var src = ""
                    if (ptype == TYPE_CSI_SRC) { if (off + 6 > bodyLen) throw ProtocolError("length: frame src"); src = macStr(buf, off); off += 6 }
                    if (off + dlen > bodyLen) throw ProtocolError("length: frame data")
                    frames.add(CsiFrame(tUs, rssi, noise, sig, cwb, buf.copyOfRange(off, off + dlen), src))
                    off += dlen
                }
            }
            TYPE_TELEM -> {
                if (n != 1 || off + 30 > bodyLen) throw ProtocolError("length: telemetry")
                val bb = ByteBuffer.wrap(buf, off, 30).order(ByteOrder.LITTLE_ENDIAN)
                telemetry = Telemetry(bb.float, bb.float, bb.get().toInt(), u32(buf, off + 9), u32(buf, off + 13),
                    u32(buf, off + 17), u32(buf, off + 21), u32(buf, off + 25), buf[off + 29].toInt() and 0xFF)
                off += 30
            }
            TYPE_PRED -> {
                if (n != 1 || off + 17 > bodyLen) throw ProtocolError("length: prediction")
                val modelVer = u32(buf, off); val inferMs = u16(buf, off + 4); val links = buf[off + 6].toInt() and 0xFF
                val act = FloatArray(4) { (buf[off + 8 + it].toInt() and 0xFF) / 255f }
                val zone = FloatArray(4) { (buf[off + 12 + it].toInt() and 0xFF) / 255f }
                val move = (buf[off + 16].toInt() and 0xFF) / 255f
                prediction = Prediction(modelVer, inferMs, links, act, zone, move); off += 17
            }
            TYPE_MODEL_ACK -> {
                if (n != 1 || off + 11 > bodyLen) throw ProtocolError("length: model ack")
                modelAck = ModelAck(u32(buf, off), u32(buf, off + 4), buf[off + 8].toInt() and 0xFF,
                    buf[off + 9].toInt() and 0xFF, buf[off + 10].toInt() and 0xFF); off += 11
            }
            TYPE_LOG -> {
                if (n != 1 || off + 1 > bodyLen) throw ProtocolError("length: log")
                val ln = buf[off].toInt() and 0xFF
                if (off + 1 + ln > bodyLen) throw ProtocolError("length: log")
                log = String(buf, off + 1, ln, Charsets.UTF_8); off += 1 + ln
            }
            TYPE_SURVEY -> {
                if (n > SURVEY_MAX || off + 3 + n * 14 != bodyLen) throw ProtocolError("length: survey")
                val state = buf[off].toInt() and 0xFF; val prog = buf[off + 1].toInt() and 0xFF
                val resp = buf[off + 2].toInt() and 0xFF; off += 3
                val es = ArrayList<SurveyEntry>()
                repeat(n) {
                    val ip = ipStr(buf, off)
                    val macRaw = buf.copyOfRange(off + 4, off + 10)
                    val mac = if (macRaw.all { it.toInt() == 0 }) "" else macStr(macRaw)
                    val y = (buf[off + 10].toInt() and 0xFF) / 100f
                    val rssi = buf[off + 11].toInt(); val ht = buf[off + 12].toInt() and 0xFF; val fl = buf[off + 13].toInt() and 0xFF
                    es.add(SurveyEntry(ip, mac, y, rssi, ht, fl)); off += 14
                }
                survey = Survey(state, prog, resp, es)
            }
            TYPE_DIAG -> { off = bodyLen /* the hub does not decode diag; it forwards/ignores */ }
            else -> throw ProtocolError("unsupported type $ptype")
        }
        if (ptype != TYPE_DIAG && off != bodyLen) throw ProtocolError("length: trailing bytes")
        return Packet(ptype, dev, bootId, seq, frames, telemetry, prediction, modelAck, log, survey)
    }

    private fun header(type: Int, dev: String, bootId: Long, seq: Long, n: Int): ByteArray {
        val b = ByteBuffer.allocate(HEADER_LEN).order(ByteOrder.LITTLE_ENDIAN)
        b.put(MAGIC.toByteArray(Charsets.US_ASCII)); b.put(VERSION.toByte()); b.put(type.toByte())
        b.put(macBytes(dev)); b.putInt(bootId.toInt()); b.putInt(seq.toInt()); b.put(n.toByte())
        return b.array()
    }

    private fun sign(key: ByteArray, body: ByteArray): ByteArray = body + hmac16(key, body)

    fun encodeModelChunk(dev: String, boardBootId: Long, seq: Long, key: ByteArray, version: Long, total: Long,
                         offset: Long, data: ByteArray): ByteArray {
        if (data.size > MODEL_CHUNK_MAX || offset + data.size > total) throw ProtocolError("chunk bounds")
        val b = ByteBuffer.allocate(14).order(ByteOrder.LITTLE_ENDIAN)
        b.putInt(version.toInt()); b.putInt(total.toInt()); b.putInt(offset.toInt()); b.putShort(data.size.toShort())
        return sign(key, header(TYPE_MODEL_CHUNK, dev, boardBootId, seq, 1) + b.array() + data)
    }

    /** Hub -> board: send everything to ip:port from now on. boot_id 0; seq must grow across the hub's life. */
    fun encodeHub(dev: String, seq: Long, key: ByteArray, ip: String, port: Int): ByteArray {
        if (port !in 1..65535) throw ProtocolError("port")
        val b = ByteBuffer.allocate(6).order(ByteOrder.LITTLE_ENDIAN)
        b.put(ipBytes(ip)); b.putShort(port.toShort())
        return sign(key, header(TYPE_HUB, dev, 0, seq, 1) + b.array())
    }

    fun encodeCmd(dev: String, boardBootId: Long, seq: Long, key: ByteArray, cmd: Int, args: ByteArray): ByteArray {
        if (args.size > 96) throw ProtocolError("command args too long")
        return sign(key, header(TYPE_CMD, dev, boardBootId, seq, 1) + byteArrayOf(cmd.toByte(), args.size.toByte()) + args)
    }

    fun surveyArgs(seconds: Int = 60, maxLinks: Int = 6, exclude: List<String> = emptyList()): ByteArray {
        if (exclude.size > CMD_SURVEY_MAX_EXCL) throw ProtocolError("too many excluded hosts")
        if (seconds !in 10..255 || maxLinks !in 0..8) throw ProtocolError("survey bounds")
        val out = java.io.ByteArrayOutputStream()
        out.write(seconds); out.write(maxLinks); out.write(exclude.size)
        for (ip in exclude) out.write(ipBytes(ip))
        return out.toByteArray()
    }

    fun linksArgs(links: List<Pair<String, String>>): ByteArray {
        if (links.size > 9) throw ProtocolError("too many links")
        val out = java.io.ByteArrayOutputStream()
        out.write(links.size)
        for ((mac, ip) in links) { out.write(macBytes(mac)); out.write(ipBytes(ip)) }
        return out.toByteArray()
    }
}
