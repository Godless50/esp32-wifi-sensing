package ru.wsn.capture.hub

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import org.json.JSONObject
import ru.wsn.capture.core.HubEngine
import ru.wsn.capture.core.Wsn1
import ru.wsn.capture.core.ZoneModel
import java.io.File
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/** The phone running as the 24/7 hub: it holds the UDP socket the boards send to (port 5005), keeps Wi-Fi and
 *  the CPU awake, turns the stream into a room verdict once a second (HubEngine), records the raw datagrams for
 *  later training, and tells the boards its own address so they follow it if the phone's IP changes. All the
 *  logic is in HubEngine (unit-tested); this class is only the Android plumbing. */
class HubService : Service() {
    companion object {
        const val PORT = 5005
        const val CHANNEL = "wsn-hub"
        const val ACTION_VERDICT = "ru.wsn.capture.VERDICT"
        private const val TAG = "HubService"
    }

    private val running = AtomicBoolean(false)
    private lateinit var engine: HubEngine
    private var socket: DatagramSocket? = null
    private var multicast: WifiManager.MulticastLock? = null
    private var wifiLock: WifiManager.WifiLock? = null
    private var wake: PowerManager.WakeLock? = null
    private val pool = Executors.newScheduledThreadPool(2)
    private var sink: java.io.OutputStream? = null
    private var hubSeq = System.currentTimeMillis() / 1000

    private fun keys(): Map<String, ByteArray> {
        val f = File(filesDir, "device_keys.json")
        if (!f.exists()) return emptyMap()
        return try {
            val j = JSONObject(f.readText())
            j.keys().asSequence().associateWith { k -> hex(j.getJSONObject(k).optString("key", "")) }
                .filterValues { it.size == 32 }
        } catch (e: Exception) { Log.w(TAG, "keys unreadable", e); emptyMap() }
    }

    private fun hex(s: String) = if (s.length % 2 != 0) ByteArray(0) else
        ByteArray(s.length / 2) { ((s[it * 2].digitToInt(16) shl 4) or s[it * 2 + 1].digitToInt(16)).toByte() }

    override fun onCreate() {
        super.onCreate()
        val km = keys()
        val model = loadModel()
        val dir = File(getExternalFilesDir(null), "recordings").apply { mkdirs() }
        sink = File(dir, "hub-${System.currentTimeMillis()}.wsn").outputStream().buffered()
        val out = sink!!
        engine = HubEngine(model = model, quiet = quietFromMeta(), keyFor = { km[it] },
            rawSink = { buf, t ->
                synchronized(out) {  // length-prefixed records: u64 ms, u16 len, bytes
                    val ms = (t * 1000).toLong()
                    for (i in 0 until 8) out.write(((ms shr (8 * i)) and 0xFF).toInt())
                    out.write(buf.size and 0xFF); out.write((buf.size shr 8) and 0xFF); out.write(buf)
                }
            })
    }

    private fun loadModel(): ZoneModel? {
        val f = File(filesDir, "model/model.json")
        return if (f.exists()) try { ZoneModel.fromJson(f.readText()) } catch (e: Exception) { null } else null
    }

    private fun quietFromMeta(): Map<String, DoubleArray> {
        val f = File(filesDir, "model/model.json")
        if (!f.exists()) return emptyMap()
        return try {
            val q = JSONObject(f.readText()).optJSONObject("quiet") ?: return emptyMap()
            q.keys().asSequence().associateWith { k -> q.getJSONArray(k).let { a -> DoubleArray(a.length()) { a.getDouble(it) } } }
        } catch (e: Exception) { emptyMap() }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (running.getAndSet(true)) return START_STICKY
        startForeground(1, notification())
        acquireLocks()
        pool.execute { receiveLoop() }
        pool.scheduleAtFixedRate({ tickOnce() }, 1, 1, TimeUnit.SECONDS)
        pool.scheduleAtFixedRate({ announce() }, 2, 15, TimeUnit.SECONDS)
        return START_STICKY
    }

    private fun receiveLoop() {
        try {
            val s = DatagramSocket(null).apply { reuseAddress = true; broadcast = true; bind(java.net.InetSocketAddress(PORT)) }
            socket = s
            val buf = ByteArray(2048)
            while (running.get()) {
                val pkt = DatagramPacket(buf, buf.size)
                s.receive(pkt)
                engine.onDatagram(pkt.data.copyOf(pkt.length), System.currentTimeMillis() / 1000.0)
            }
        } catch (e: Exception) { if (running.get()) Log.e(TAG, "receive loop", e) }
    }

    private fun tickOnce() {
        try {
            val v = engine.tick(System.currentTimeMillis() / 1000.0) ?: return
            sendBroadcast(Intent(ACTION_VERDICT).setPackage(packageName)
                .putExtra("zone", v.zone).putExtra("zoneP", v.zoneP).putExtra("emptyP", v.emptyP)
                .putExtra("movingP", v.movingP).putExtra("linksLive", v.linksLive))
        } catch (e: Exception) { Log.w(TAG, "tick", e) }
    }

    /** Tell every board whose key we hold where to send: a signed WSN_TYPE_HUB broadcast to the subnet. */
    private fun announce() {
        val s = socket ?: return
        val ip = localIp() ?: return
        hubSeq++
        for ((mac, key) in keys()) {
            try {
                val pkt = Wsn1.encodeHub(mac, hubSeq, key, ip, PORT)
                s.send(DatagramPacket(pkt, pkt.size, InetAddress.getByName("255.255.255.255"), PORT))
            } catch (e: Exception) { Log.w(TAG, "announce $mac", e) }
        }
    }

    private fun localIp(): String? {
        val wifi = getSystemService(Context.WIFI_SERVICE) as WifiManager
        @Suppress("DEPRECATION") val ipInt = wifi.connectionInfo.ipAddress
        if (ipInt == 0) return null
        return "${ipInt and 0xFF}.${(ipInt shr 8) and 0xFF}.${(ipInt shr 16) and 0xFF}.${(ipInt shr 24) and 0xFF}"
    }

    private fun acquireLocks() {
        val wifi = getSystemService(Context.WIFI_SERVICE) as WifiManager
        multicast = wifi.createMulticastLock("wsn-hub").apply { setReferenceCounted(false); acquire() }
        @Suppress("DEPRECATION")
        wifiLock = wifi.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "wsn-hub").apply { acquire() }
        val power = getSystemService(Context.POWER_SERVICE) as PowerManager
        wake = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "wsn:hub").apply { acquire() }
    }

    private fun notification(): Notification {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Датчик присутствия", NotificationManager.IMPORTANCE_LOW))
        }
        @Suppress("DEPRECATION")
        val b = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) Notification.Builder(this, CHANNEL) else Notification.Builder(this)
        return b.setContentTitle("Датчик присутствия работает")
            .setContentText("Телефон слушает платы и определяет комнаты")
            .setSmallIcon(android.R.drawable.ic_menu_compass).setOngoing(true).build()
    }

    override fun onDestroy() {
        running.set(false)
        pool.shutdownNow()
        try { socket?.close() } catch (_: Exception) {}
        try { sink?.close() } catch (_: Exception) {}
        try { multicast?.release() } catch (_: Exception) {}
        try { wifiLock?.release() } catch (_: Exception) {}
        try { wake?.release() } catch (_: Exception) {}
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
