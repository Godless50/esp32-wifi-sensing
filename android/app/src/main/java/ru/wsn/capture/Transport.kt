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
/** What the capture screen talks through: the WebSocket to a computer server, or the hub inside this phone. */
interface CaptureLink {
    fun connect()
    fun send(text: String): Boolean
    fun close()
}

class Transport(
    private val path: String,
    private val onOpen: () -> Unit,
    private val onMessage: (String) -> Unit,
    private val onClosed: (reason: String) -> Unit,
) : CaptureLink {
    private val main = Handler(Looper.getMainLooper())
    private var ws: WebSocket? = null
    private var stopped = false

    override fun connect() {
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

    override fun send(text: String): Boolean = ws?.send(text) ?: false

    override fun close() {
        stopped = true
        ws?.close(1000, "bye")
        ws = null
    }
}

/** The capture protocol served by the hub service in this phone (LocalCapture): no network, the same messages. */
class LocalLink(
    private val clientId: String,
    private val onOpen: () -> Unit,
    private val onMessage: (String) -> Unit,
    private val onClosed: (reason: String) -> Unit,
) : CaptureLink {
    private val main = Handler(Looper.getMainLooper())
    private var open = false

    override fun connect() {
        val hub = ru.wsn.capture.hub.HubService.current
        if (hub == null) { onClosed("центр не запущен"); main.postDelayed({ connect() }, 2000); return }
        open = true
        main.post { onOpen() }
    }

    override fun send(text: String): Boolean {
        val hub = ru.wsn.capture.hub.HubService.current ?: return false
        if (!open) return false
        val replies = synchronized(hub.local) { hub.local.onMessage(clientId, text) }
        for (r in replies) main.post { onMessage(r) }
        return true
    }

    override fun close() { open = false }
}
