package ru.wsn.capture

import android.os.Handler
import android.os.Looper
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

/**
 * WebSocket to the paired server (TLS trust and password come from [Server.http]) with automatic reconnect (2 s).
 * Callbacks run on the main thread.
 */
class Transport(
    private val path: String,
    private val onOpen: () -> Unit,
    private val onMessage: (String) -> Unit,
    private val onClosed: (reason: String) -> Unit,
) {
    private val main = Handler(Looper.getMainLooper())
    private var ws: WebSocket? = null
    private var stopped = false

    fun connect() {
        stopped = false
        val client = try { Server.http() } catch (e: Exception) { onClosed("не сопряжено"); return }
        val req = Request.Builder().url(Server.wsUrl(path)).build()
        ws = client.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) = main.post { onOpen() }.let { }
            override fun onMessage(webSocket: WebSocket, text: String) = main.post { onMessage(text) }.let { }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = lost("closed $code $reason")
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) =
                lost(response?.let { if (it.code == 401) "неверный пароль" else "HTTP ${it.code}" } ?: (t.message ?: t.javaClass.simpleName))
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
