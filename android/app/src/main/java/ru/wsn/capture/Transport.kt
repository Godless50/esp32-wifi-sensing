package ru.wsn.capture

import android.os.Handler
import android.os.Looper
import okhttp3.Credentials
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.TimeUnit

/**
 * WebSocket to /ws/capture with basic auth and automatic reconnect (2 s).
 * TLS trust comes from network_security_config (bundled Caddy root CA). Callbacks run on the main thread.
 */
class Transport(
    private val url: String,
    private val user: String,
    private val password: String,
    private val onOpen: () -> Unit,
    private val onMessage: (String) -> Unit,
    private val onClosed: (reason: String) -> Unit,
) {
    private val main = Handler(Looper.getMainLooper())
    private val http = OkHttpClient.Builder()
        .pingInterval(15, TimeUnit.SECONDS)
        .connectTimeout(5, TimeUnit.SECONDS)
        .build()
    private var ws: WebSocket? = null
    private var stopped = false

    fun connect() {
        stopped = false
        val req = Request.Builder().url(url).header("Authorization", Credentials.basic(user, password)).build()
        ws = http.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) = main.post { onOpen() }.let { }
            override fun onMessage(webSocket: WebSocket, text: String) = main.post { onMessage(text) }.let { }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = lost("closed $code $reason")
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) =
                lost(response?.let { "HTTP ${it.code}" } ?: (t.message ?: t.javaClass.simpleName))
        })
    }

    private fun lost(reason: String) {
        main.post {
            onClosed(reason)
            if (!stopped) main.postDelayed({ if (!stopped) connect() }, 2000)
        }
    }

    fun send(text: String): Boolean = ws?.send(text) ?: false

    fun close() {
        stopped = true
        ws?.close(1000, "bye")
        ws = null
    }
}
