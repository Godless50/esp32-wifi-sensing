package ru.wsn.capture

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PairingTest {
    // самоподписанный тестовый сертификат; отпечаток и QR посчитаны серверным wsn.pairing (Python ssl) —
    // значит, приложение и install.sh считают одно и то же
    private val pem = """
        -----BEGIN CERTIFICATE-----
        MIIBgzCCASmgAwIBAgIUDLmKkWDYWp6EqeFIPU4BN54UGkkwCgYIKoZIzj0EAwIw
        FjEUMBIGA1UEAwwLV1NOIHRlc3QgQ0EwIBcNMjYxMDE0MTczMjA0WhgPMjEyNjA5
        MjAxNzMyMDRaMBYxFDASBgNVBAMMC1dTTiB0ZXN0IENBMFkwEwYHKoZIzj0CAQYI
        KoZIzj0DAQcDQgAEovQX5knneCVJUdiVSap9R/3pwuMGoy59YpQ0+ind5Ouz6M69
        pHftHjudzDavJXo4n3maIvbvF31yf2kyxZorKqNTMFEwHQYDVR0OBBYEFJWsjFCW
        FA39Ec3FPnzes0h6ANTBMB8GA1UdIwQYMBaAFJWsjFCWFA39Ec3FPnzes0h6ANTB
        MA8GA1UdEwEB/wQFMAMBAf8wCgYIKoZIzj0EAwIDSAAwRQIhAL1FStfbMBf9M1BV
        k1gihXoCxut0Uwosw5St0UY9wP2xAiAZzlKTIUu5/eUqSZBri/Xu/ywuIqVNFbva
        F4m1g+HE2w==
        -----END CERTIFICATE-----
    """.trimIndent()
    private val fp = "36e9ae4b5c7f7a33f7a97d1019fb579d173b9c441fed9690711f70c371f6aca2"

    @Test fun fingerprintMatchesServer() {
        assertEquals(fp, PairInfo.sha256Hex(PairInfo.parsePem(pem).encoded))
    }

    @Test fun parsesQrFromInstallScript() {
        val p = PairInfo.parse("wsn://pair?h=192.168.1.10&p=8443&ca=$fp")!!
        assertEquals("192.168.1.10", p.host)
        assertEquals(8443, p.port)
        assertTrue(p.matches(fp))
        assertFalse(p.matches("00" + fp.drop(2)))
    }

    @Test fun shortCodeTypedByHand() {
        val p = PairInfo.fromParts("192.168.1.10", "8443", "36E9-AE4B-5C7F-7A33")!!
        assertTrue(p.matches(fp))
        assertFalse(p.matches("36e9ae4b5c7f7a34" + fp.drop(16)))
    }

    @Test fun rejectsForeignCodes() {
        assertNull(PairInfo.parse("https://example.com/?h=1&ca=$fp"))
        assertNull(PairInfo.parse("wsn://pair?h=192.168.1.10&ca=1234"))       // слишком короткий код
        assertNull(PairInfo.parse("wsn://pair?ca=$fp"))                        // нет адреса
        assertNull(PairInfo.fromParts("h", "8443", "zzzzzzzzzzzzzzzzzz"))      // не hex
    }
}
