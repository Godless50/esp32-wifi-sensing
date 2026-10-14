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
import ru.wsn.capture.core.LocalCapture
import ru.wsn.capture.core.TrainPipeline
import ru.wsn.capture.core.Wsn1
import ru.wsn.capture.core.ZoneModel
import java.io.File
import java.io.OutputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/** The phone running as the 24/7 hub: it holds the UDP socket the boards send to (port 5005), keeps Wi-Fi and
 *  the CPU awake, turns the stream into a room verdict once a second (HubEngine), tells the boards its own address
 *  so they follow it if the phone's IP changes, records the raw datagrams only while a walk is being recorded
 *  (LocalCapture session), sends the boards their survey commands, and trains the model on the phone.
 *  The logic is in the tested core classes; this is the Android plumbing. The UI reaches it through [current]. */
class HubService : Service() {
    companion object {
        const val PORT = 5005
        const val CHANNEL = "wsn-hub"
        private const val TAG = "HubService"
        @Volatile var current: HubService? = null
            private set

        fun start(ctx: Context) {
            val i = Intent(ctx, HubService::class.java)
            if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i) else ctx.startService(i)
        }
    }

    data class Board(val mac: String, val name: String, val ageS: Double, val bootId: Long, val addr: InetSocketAddress)
    data class TrainState(val running: Boolean, val stage: String, val progress: Double, val message: String,
                          val ok: Boolean? = null)

    private val running = AtomicBoolean(false)
    lateinit var engine: HubEngine
        private set
    lateinit var local: LocalCapture
        private set
    private var socket: DatagramSocket? = null
    private var multicast: WifiManager.MulticastLock? = null
    private var wifiLock: WifiManager.WifiLock? = null
    private var wake: PowerManager.WakeLock? = null
    private val pool = Executors.newScheduledThreadPool(3)
    private val sinkLock = Any()
    private var sink: OutputStream? = null
    private var hubSeq = System.currentTimeMillis() / 1000
    private var cmdSeq = System.currentTimeMillis() and 0x7FFFFFFF
    private val seen = ConcurrentHashMap<String, Triple<Double, Long, InetSocketAddress>>()  // mac -> (t, boot, addr)
    val surveys = ConcurrentHashMap<String, Wsn1.Survey>()
    @Volatile var verdict: ZoneModel.Verdict? = null
        private set
    @Volatile var train = TrainState(false, "", 0.0, "")
        private set

    fun now() = System.currentTimeMillis() / 1000.0

    val sessionsDir: File get() = File(getExternalFilesDir(null), "sessions").apply { mkdirs() }
    private val modelFile: File get() = File(filesDir, "model/model.json")

    // ---------------- board keys (written by the cable setup) ----------------
    private val keysFile: File get() = File(filesDir, "device_keys.json")
    @Volatile private var keyCache: Map<String, ByteArray> = emptyMap()

    fun keys(): Map<String, ByteArray> {
        if (!keysFile.exists()) return emptyMap()
        return try {
            val j = JSONObject(keysFile.readText())
            j.keys().asSequence().associateWith { k -> hex(j.getJSONObject(k).optString("key", "")) }.filterValues { it.size == 32 }
        } catch (e: Exception) { Log.w(TAG, "keys unreadable", e); emptyMap() }
    }

    @Synchronized fun addKey(mac: String, keyHex: String, name: String) {
        val j = if (keysFile.exists()) JSONObject(keysFile.readText()) else JSONObject()
        j.put(mac.lowercase(), JSONObject().put("key", keyHex).put("name", name))
        keysFile.writeText(j.toString())
        keyCache = keys()
    }

    fun knownBoards(): List<String> = keyCache.keys.sorted()

    fun boardName(mac: String): String = try {
        JSONObject(keysFile.readText()).optJSONObject(mac)?.optString("name") ?: mac
    } catch (e: Exception) { mac }

    private fun hex(s: String) = if (s.length % 2 != 0) ByteArray(0) else
        ByteArray(s.length / 2) { ((s[it * 2].digitToInt(16) shl 4) or s[it * 2 + 1].digitToInt(16)).toByte() }

    // ---------------- lifecycle ----------------
    override fun onCreate() {
        super.onCreate()
        keyCache = keys()
        engine = HubEngine(model = loadModel(), quiet = quietFromModel(), keyFor = { keyCache[it] },
            rawSink = { buf, t -> record(buf, t) })
        local = LocalCapture(sessionsDir, ::now, { boards().map { it.mac to it.name } }, ::onSession)
        current = this
    }

    private fun record(buf: ByteArray, t: Double) {
        synchronized(sinkLock) {
            val out = sink ?: return  // пишем только во время записи обхода: сутки потока — гигабайты
            val ms = (t * 1000).toLong()
            for (i in 0 until 8) out.write(((ms shr (8 * i)) and 0xFF).toInt())
            out.write(buf.size and 0xFF); out.write((buf.size shr 8) and 0xFF); out.write(buf)
        }
    }

    private fun onSession(dir: File?) {
        synchronized(sinkLock) {
            try { sink?.close() } catch (_: Exception) {}
            sink = dir?.let { File(it, "csi.wsn").outputStream().buffered(1 shl 16) }
        }
    }

    private fun loadModel(): ZoneModel? =
        if (modelFile.exists()) try { ZoneModel.fromJson(modelFile.readText()) } catch (e: Exception) { null } else null

    fun modelMeta(): JSONObject? = if (modelFile.exists()) try { JSONObject(modelFile.readText()) } catch (e: Exception) { null } else null

    private fun quietFromModel(): Map<String, DoubleArray> {
        val q = modelMeta()?.optJSONObject("quiet") ?: return emptyMap()
        return q.keys().asSequence().associateWith { k -> q.getJSONArray(k).let { a -> DoubleArray(a.length()) { a.getDouble(it) } } }
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
            val s = DatagramSocket(null).apply { reuseAddress = true; broadcast = true; bind(InetSocketAddress(PORT)) }
            socket = s
            val buf = ByteArray(2048)
            while (running.get()) {
                val pkt = DatagramPacket(buf, buf.size)
                s.receive(pkt)
                val t = now()
                val p = engine.onDatagram(pkt.data.copyOf(pkt.length), t) ?: continue
                seen[p.deviceId] = Triple(t, p.bootId, InetSocketAddress(pkt.address, pkt.port))
                if (p.type == Wsn1.TYPE_SURVEY) p.survey?.let { surveys[p.deviceId] = it }
            }
        } catch (e: Exception) { if (running.get()) Log.e(TAG, "receive loop", e) }
    }

    private fun tickOnce() {
        try { verdict = engine.tick(now()) } catch (e: Exception) { Log.w(TAG, "tick", e) }
    }

    /** Boards that sent something in the last `withinS` seconds (only boards whose key we hold get through). */
    fun boards(withinS: Double = 15.0): List<Board> {
        val t = now()
        return seen.entries.filter { t - it.value.first <= withinS }.sortedBy { it.key }
            .map { Board(it.key, boardName(it.key), t - it.value.first, it.value.second, it.value.third) }
    }

    /** Tell every board whose key we hold where to send: a signed WSN_TYPE_HUB broadcast to the subnet. */
    fun announce() {
        val s = socket ?: return
        val ip = localIp() ?: return
        hubSeq++
        for ((mac, key) in keyCache) {
            try {
                val pkt = Wsn1.encodeHub(mac, hubSeq, key, ip, PORT)
                s.send(DatagramPacket(pkt, pkt.size, InetAddress.getByName("255.255.255.255"), PORT))
                seen[mac]?.let { s.send(DatagramPacket(pkt, pkt.size, it.third)) }  // и напрямую: широковещание теряется
            } catch (e: Exception) { Log.w(TAG, "announce $mac", e) }
        }
    }

    private fun sendCmd(mac: String, cmd: Int, args: ByteArray): Boolean {
        val s = socket ?: return false
        val b = seen[mac] ?: return false
        val key = keyCache[mac] ?: return false
        cmdSeq++
        val pkt = Wsn1.encodeCmd(mac, b.second, cmdSeq, key, cmd, args)
        pool.execute { try { repeat(3) { s.send(DatagramPacket(pkt, pkt.size, b.third)); Thread.sleep(50) } } catch (_: Exception) {} }
        return true
    }

    /** "Quiet mode": the board finds the home devices it hears and keeps the good ones as its links (~1 min). */
    fun survey(mac: String, seconds: Int = 60, maxLinks: Int = 6): Boolean {
        surveys.remove(mac)
        return sendCmd(mac, Wsn1.CMD_SURVEY, Wsn1.surveyArgs(seconds, maxLinks, listOfNotNull(localIp())))
    }

    fun localIp(): String? {
        val wifi = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        @Suppress("DEPRECATION") val ipInt = wifi.connectionInfo.ipAddress
        if (ipInt == 0) return null
        return "${ipInt and 0xFF}.${(ipInt shr 8) and 0xFF}.${(ipInt shr 16) and 0xFF}.${(ipInt shr 24) and 0xFF}"
    }

    /** Sessions recorded on this phone, newest first. */
    fun sessions(): List<File> = (sessionsDir.listFiles() ?: emptyArray()).filter { File(it, "meta.json").exists() }
        .sortedByDescending { it.name }

    /** Train on the newest finished walk, in the background; [train] follows the progress. */
    fun trainNewest() {
        if (train.running) return
        val dir = sessions().firstOrNull { JSONObject(File(it, "meta.json").readText()).has("t1") }
        if (dir == null) { train = TrainState(false, "", 0.0, "нет записанного обхода", false); return }
        train = TrainState(true, "чтение записи", 0.0, "")
        pool.execute {
            try {
                val s = TrainPipeline.loadSession(dir)
                val r = TrainPipeline.train(s, progress = { st, p -> train = TrainState(true, st, p, "") })
                modelFile.parentFile?.mkdirs()
                val tmp = File(modelFile.parentFile, "model.json.tmp")
                tmp.writeText(r.modelJson); tmp.renameTo(modelFile)
                engine.setQuiet(quietFromModel())
                engine.model = loadModel()
                train = TrainState(false, "готово", 1.0, r.message, true)
            } catch (e: TrainPipeline.TrainError) {
                train = TrainState(false, "", 0.0, e.message ?: "ошибка", false)
            } catch (e: Exception) {
                Log.e(TAG, "train", e)
                train = TrainState(false, "", 0.0, "сбой обучения: ${e.message}", false)
            }
        }
    }

    private fun acquireLocks() {
        val wifi = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
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
        current = null
        synchronized(local) { local.abort() }
        pool.shutdownNow()
        try { socket?.close() } catch (_: Exception) {}
        synchronized(sinkLock) { try { sink?.close() } catch (_: Exception) {}; sink = null }
        try { multicast?.release() } catch (_: Exception) {}
        try { wifiLock?.release() } catch (_: Exception) {}
        try { wake?.release() } catch (_: Exception) {}
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
