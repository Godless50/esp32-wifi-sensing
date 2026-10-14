package ru.wsn.capture

import android.annotation.SuppressLint
import android.content.Context
import android.content.SharedPreferences
import android.os.Handler
import android.os.Looper
import okhttp3.Call
import okhttp3.Callback
import okhttp3.Credentials
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import org.json.JSONObject
import java.io.IOException
import java.security.KeyStore
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager

/**
 * The paired server: address, password, and TLS trust in exactly the server's own CA (from pairing).
 * Every HTTP / WebSocket call of the app goes through [http]. Callbacks of [getJson] run on the main thread.
 */
object Server {
    private lateinit var prefs: SharedPreferences
    private var client: OkHttpClient? = null
    private val main = Handler(Looper.getMainLooper())
    const val USER = "wsn"

    fun init(ctx: Context) {
        if (!::prefs.isInitialized) prefs = ctx.applicationContext.getSharedPreferences("wsn", Context.MODE_PRIVATE)
    }

    val host: String? get() = prefs.getString("host", null)
    val port: Int get() = prefs.getInt("port", 8443)
    val password: String get() = prefs.getString("password", "") ?: ""
    private val caPem: String? get() = prefs.getString("ca_pem", null)
    fun paired(): Boolean = host != null && caPem != null && password.isNotEmpty()
    fun address(): String = "${host ?: "?"}:$port"
    fun url(path: String): String = "https://$host:$port$path"
    fun wsUrl(path: String): String = "wss://$host:$port$path"

    fun setPassword(pw: String) {
        prefs.edit().putString("password", pw).apply()
        client = null
    }

    /** Client that trusts only the paired CA (+ hostname check) and sends the password. */
    fun http(): OkHttpClient {
        client?.let { return it }
        val pem = caPem ?: throw IllegalStateException("not paired")
        val tm = trustOnly(PairInfo.parsePem(pem))
        val ctx = SSLContext.getInstance("TLS").apply { init(null, arrayOf(tm), null) }
        val auth = Credentials.basic(USER, password)
        return OkHttpClient.Builder()
            .sslSocketFactory(ctx.socketFactory, tm)
            .addInterceptor { chain -> chain.proceed(chain.request().newBuilder().header("Authorization", auth).build()) }
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(15, TimeUnit.SECONDS)
            .pingInterval(15, TimeUnit.SECONDS)
            .build().also { client = it }
    }

    private fun trustOnly(ca: X509Certificate): X509TrustManager {
        val ks = KeyStore.getInstance(KeyStore.getDefaultType()).apply { load(null, null); setCertificateEntry("wsn", ca) }
        val tmf = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()).apply { init(ks) }
        return tmf.trustManagers.first { it is X509TrustManager } as X509TrustManager
    }

    /** Trust-anything client — ONLY to download /ca.crt, whose content is then checked against the QR hash. */
    @SuppressLint("CustomX509TrustManager", "TrustAllX509TrustManager")
    private fun bootstrapClient(): OkHttpClient {
        val any = object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {}
            override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {}
            override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
        }
        val ctx = SSLContext.getInstance("TLS").apply { init(null, arrayOf(any), null) }
        return OkHttpClient.Builder().sslSocketFactory(ctx.socketFactory, any).hostnameVerifier { _, _ -> true }
            .connectTimeout(5, TimeUnit.SECONDS).readTimeout(10, TimeUnit.SECONDS).build()
    }

    /**
     * Pair: download the server's CA, accept it only if its SHA-256 matches the QR code, then check the password
     * with GET /api/status. [done] gets null on success or a message for the person (main thread).
     */
    fun pair(info: PairInfo, pw: String, done: (String?) -> Unit) {
        val req = Request.Builder().url("https://${info.host}:${info.port}/ca.crt").build()
        bootstrapClient().newCall(req).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) =
                post(done, "Сервер ${info.host}:${info.port} недоступен: ${e.message}. Телефон и компьютер в одной сети?")

            override fun onResponse(call: Call, response: Response) {
                val pem = response.use { if (it.isSuccessful) it.body?.string() else null }
                if (pem == null || !pem.contains("BEGIN CERTIFICATE")) {
                    post(done, "Сервер не отдал сертификат (HTTP ${response.code})"); return
                }
                val fp = try { PairInfo.sha256Hex(PairInfo.parsePem(pem).encoded) } catch (e: Exception) {
                    post(done, "Сертификат не читается: ${e.message}"); return
                }
                if (!info.matches(fp)) {
                    post(done, "Сертификат сервера не совпадает с QR-кодом. Это другой сервер — сопряжение отменено."); return
                }
                prefs.edit().putString("host", info.host).putInt("port", info.port).putString("ca_pem", pem)
                    .putString("password", pw).apply()
                client = null
                getJson("/api/status") { _, err ->
                    done(when {
                        err == null -> null
                        err.contains("401") -> "Неверный пароль (тот, что задан в install.sh)"
                        else -> err
                    })
                }
            }
        })
    }

    /** GET JSON; cb(json, null) or cb(null, error) on the main thread. */
    fun getJson(path: String, cb: (JSONObject?, String?) -> Unit) {
        val c = try { http() } catch (e: Exception) { cb(null, "не сопряжено"); return }
        c.newCall(Request.Builder().url(url(path)).build()).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) = main.post { cb(null, e.message ?: "нет связи") }.let { }
            override fun onResponse(call: Call, response: Response) {
                val text = response.use { if (it.isSuccessful) it.body?.string() else null }
                val js = text?.let { try { JSONObject(it) } catch (e: Exception) { null } }
                main.post { if (js != null) cb(js, null) else cb(null, "HTTP ${response.code}") }
            }
        })
    }

    private fun post(done: (String?) -> Unit, msg: String?) = main.post { done(msg) }.let { }
}
