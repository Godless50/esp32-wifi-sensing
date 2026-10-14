package ru.wsn.capture

import java.io.ByteArrayInputStream
import java.net.URI
import java.net.URLDecoder
import java.security.MessageDigest
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate

/**
 * Pairing data from the QR code of `install.sh pair`: server address and the SHA-256 of the root certificate of
 * the server's own CA. Pure logic (no Android networking), unit-tested.
 */
data class PairInfo(val host: String, val port: Int, val caSha256: String) {
    /** Full hash from the QR code, or the first 16 hex digits typed by hand ("ABCD-1234-…"). */
    fun matches(fingerprint: String): Boolean {
        val want = caSha256.lowercase().replace("-", "")
        return want.length >= 16 && fingerprint.lowercase().startsWith(want)
    }

    companion object {
        /** wsn://pair?h=192.168.1.10&p=8443&ca=<64 hex>; null when it is not our code. */
        fun parse(text: String): PairInfo? {
            val u = try { URI(text.trim()) } catch (e: Exception) { return null }
            if (u.scheme != "wsn" || u.host != "pair") return null
            val q = (u.rawQuery ?: "").split("&").mapNotNull {
                val kv = it.split("=", limit = 2)
                if (kv.size == 2) URLDecoder.decode(kv[0], "UTF-8") to URLDecoder.decode(kv[1], "UTF-8") else null
            }.toMap()
            return fromParts(q["h"], q["p"], q["ca"])
        }

        fun fromParts(host: String?, port: String?, ca: String?): PairInfo? {
            val h = host?.trim().orEmpty()
            val p = port?.trim()?.toIntOrNull() ?: 8443
            val c = ca?.trim()?.lowercase()?.replace("-", "").orEmpty()
            if (h.isEmpty() || p !in 1..65535) return null
            if (c.length < 16 || c.length > 64 || !c.all { it in '0'..'9' || it in 'a'..'f' }) return null
            return PairInfo(h, p, c)
        }

        fun sha256Hex(bytes: ByteArray): String =
            MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

        /** First certificate of a PEM text. */
        fun parsePem(pem: String): X509Certificate =
            CertificateFactory.getInstance("X.509").generateCertificate(ByteArrayInputStream(pem.toByteArray())) as X509Certificate
    }
}
