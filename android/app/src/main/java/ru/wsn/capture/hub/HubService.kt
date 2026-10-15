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
import ru.wsn.capture.core.EventLog
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
    @Volatile private var socket: DatagramSocket? = null
    private var multicast: WifiManager.MulticastLock? = null
    private var wifiLock: WifiManager.WifiLock? = null
    private var wake: PowerManager.WakeLock? = null
    private val pool = Executors.newScheduledThreadPool(3)
    private val sinkLock = Any()
    private var sink: OutputStream? = null
    // счётчики в секундах unix (u32 хватит до 2106): после перезапуска центра они всё равно больше прежних
    private val hubSeq = java.util.concurrent.atomic.AtomicLong(System.currentTimeMillis() / 1000)
    private val cmdSeq = java.util.concurrent.atomic.AtomicLong(System.currentTimeMillis() / 1000)
    private val seen = ConcurrentHashMap<String, Triple<Double, Long, InetSocketAddress>>()  // mac -> (t, boot, addr)
    val surveys = ConcurrentHashMap<String, Wsn1.Survey>()
    @Volatile var verdict: ZoneModel.Verdict? = null
        private set
    @Volatile var train = TrainState(false, "", 0.0, "")
        private set
    /** The diary of the main screen; kept in a file so a restart of the phone does not empty it. */
    val events = EventLog()
    private val eventsFile: File get() = File(filesDir, "events.json")
    /** Boards silent this long count as «нет связи» (they send many times a second). */
    val offlineAfterS = 60.0
    /** When the service started (unix seconds): right after a start nothing is heard yet, which is not «нет связи». */
    val startedAt = System.currentTimeMillis() / 1000.0

    fun now() = System.currentTimeMillis() / 1000.0

    val sessionsDir: File get() = File(getExternalFilesDir(null), "sessions").apply { mkdirs() }
    private val modelFile: File get() = File(filesDir, "model/model.json")

    // ---------------- board keys (written by the cable setup) ----------------
    private val keysFile: File get() = File(filesDir, "device_keys.json")
    @Volatile private var keyCache: Map<String, ByteArray> = emptyMap()

    @Volatile private var names: Map<String, String> = emptyMap()

    private fun keysJson(): JSONObject = try {
        if (keysFile.exists()) JSONObject(keysFile.readText()) else JSONObject()
    } catch (e: Exception) { Log.w(TAG, "keys file unreadable", e); JSONObject() }

    /** Keys entry by entry: one broken entry does not take the others with it. */
    fun keys(): Map<String, ByteArray> {
        val j = keysJson()
        val out = HashMap<String, ByteArray>(); val nm = HashMap<String, String>()
        for (k in j.keys()) try {
            val e = j.getJSONObject(k); val key = hex(e.optString("key", ""))
            if (key.size == 32) { out[k] = key; nm[k] = e.optString("name", k) }
        } catch (e: Exception) { Log.w(TAG, "key entry $k unreadable") }
        names = nm
        return out
    }

    /** Atomic: temp file, sync, rename (a half-written file would lose the keys of every board). */
    @Synchronized fun addKey(mac: String, keyHex: String, name: String) {
        val j = keysJson()
        j.put(mac.lowercase(), JSONObject().put("key", keyHex).put("name", name))
        val tmp = File(filesDir, "device_keys.json.tmp")
        java.io.FileOutputStream(tmp).use { it.write(j.toString().toByteArray()); it.fd.sync() }
        if (!tmp.renameTo(keysFile)) throw java.io.IOException("cannot store the board key")
        keyCache = keys()
    }

    fun knownBoards(): List<String> = keyCache.keys.sorted()

    fun boardName(mac: String): String = names[mac] ?: mac

    private fun hex(s: String) = if (s.length % 2 != 0 || !s.all { it in "0123456789abcdefABCDEF" }) ByteArray(0) else
        ByteArray(s.length / 2) { ((s[it * 2].digitToInt(16) shl 4) or s[it * 2 + 1].digitToInt(16)).toByte() }

    // ---------------- lifecycle ----------------
    override fun onCreate() {
        super.onCreate()
        keyCache = keys()
        engine = HubEngine(model = loadModel(), quiet = quietFromModel(), keyFor = { keyCache[it] },
            rawSink = { buf, t -> record(buf, t) })
        local = LocalCapture(sessionsDir, ::now, { boards().map { it.mac to it.name } }, ::onSession)
        try { if (eventsFile.exists()) events.load(eventsFile.readText()) } catch (e: Exception) { Log.w(TAG, "events unreadable", e) }
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

    @Volatile private var metaCache: Pair<Long, JSONObject?>? = null

    /** model.json without the weights (plan, rooms_ok, quiet, metrics); re-read only when the file changes. */
    fun modelMeta(): JSONObject? {
        if (!modelFile.exists()) return null
        val m = modelFile.lastModified()
        metaCache?.let { if (it.first == m) return it.second }
        val j = try { JSONObject(modelFile.readText()).apply { remove("nets"); remove("mu"); remove("sd") } } catch (e: Exception) { null }
        metaCache = m to j
        return j
    }

    private fun quietFromModel(): Map<String, DoubleArray> {
        val q = modelMeta()?.optJSONObject("quiet") ?: return emptyMap()
        return q.keys().asSequence().associateWith { k -> q.getJSONArray(k).let { a -> DoubleArray(a.length()) { a.getDouble(it) } } }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (running.getAndSet(true)) return START_STICKY
        if (Build.VERSION.SDK_INT >= 29) startForeground(1, notification(), android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
        else startForeground(1, notification())
        acquireLocks()
        pool.execute { receiveLoop() }
        pool.scheduleAtFixedRate({ tickOnce() }, 1, 1, TimeUnit.SECONDS)
        pool.scheduleAtFixedRate({ announce() }, 2, 15, TimeUnit.SECONDS)
        pool.scheduleAtFixedRate({ synchronized(sinkLock) { try { sink?.flush() } catch (_: Exception) {} } }, 5, 5, TimeUnit.SECONDS)
        return START_STICKY
    }

    /** Receive forever: a failed bind or a socket error is retried (the service must not go deaf). */
    private fun receiveLoop() {
        var backoff = 1000L
        while (running.get()) {
            try {
                val s = DatagramSocket(null).apply { reuseAddress = true; broadcast = true; bind(InetSocketAddress(PORT)) }
                socket = s
                backoff = 1000L
                val buf = ByteArray(2048)
                while (running.get()) {
                    val pkt = DatagramPacket(buf, buf.size)
                    s.receive(pkt)
                    val t = now()
                    val p = engine.onDatagram(pkt.data.copyOf(pkt.length), t) ?: continue  // чужое, неподписанное, повтор
                    seen[p.deviceId] = Triple(t, p.bootId, InetSocketAddress(pkt.address, pkt.port))
                    if (p.type == Wsn1.TYPE_SURVEY) p.survey?.let { surveys[p.deviceId] = it }
                }
            } catch (e: Exception) {
                if (!running.get()) return
                Log.e(TAG, "receive loop, retry in ${backoff / 1000} s", e)
                try { socket?.close() } catch (_: Exception) {}
                socket = null
                try { Thread.sleep(backoff) } catch (_: InterruptedException) { return }
                backoff = minOf(backoff * 2, 30_000L)
            }
        }
    }

    private fun tickOnce() {
        try { verdict = engine.tick(now()) } catch (e: Exception) { Log.w(TAG, "tick", e) }
        try { diary() } catch (e: Exception) { Log.w(TAG, "events", e) }
    }

    /** One state per second into the diary: no board heard for a minute is «нет связи», not «никого»;
     *  the phone away from the home Wi-Fi is «нет данных», not «нет связи». */
    private fun diary() {
        if (keyCache.isEmpty() || modelMeta() == null) return     // до конца настройки дневник не ведём
        val t = now()
        val v = verdict
        val e = when {
            isAway() -> events.feed(t, EventLog.State.NODATA)
            boards(offlineAfterS).isEmpty() && lastHeard() == null && t - startedAt < offlineAfterS -> null
            boards(offlineAfterS).isEmpty() -> events.feed(t, EventLog.State.OFFLINE)
            v == null -> null
            v.emptyP >= 0.5 -> events.feed(t, EventLog.State.EMPTY)
            else -> events.feed(t, if (v.movingP >= 0.5) EventLog.State.MOVING else EventLog.State.QUIET,
                if (modelMeta()?.optBoolean("rooms_ok") == true) v.zone else null)
        }
        if (e != null) {
            val tmp = File(filesDir, "events.json.tmp")
            tmp.writeText(events.toJson()); tmp.renameTo(eventsFile)
        }
    }

    // ---------------- home network ----------------
    private val prefs get() = getSharedPreferences("wsn-hub", Context.MODE_PRIVATE)

    /** The Wi-Fi the boards were set up on (saved after a successful cable setup). */
    var homeSsid: String?
        get() = prefs.getString("home_ssid", null)
        set(v) { prefs.edit().putString("home_ssid", v).apply() }

    @Suppress("DEPRECATION")
    fun currentSsid(): String? {
        val wifi = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        val s = wifi.connectionInfo?.ssid ?: return null
        return s.removeSurrounding("\"").takeIf { it.isNotEmpty() && it != "<unknown ssid>" }
    }

    /** The phone is not on the home network: no address, or (when the name is readable) another network.
     *  Boards heard = at home, whatever the network is called (2,4 and 5 GHz often have different names). */
    fun isAway(): Boolean {
        if (localIp() == null) return true
        if (boards(offlineAfterS).isNotEmpty()) return false
        val home = homeSsid ?: return false
        val cur = currentSsid() ?: return false
        return cur != home
    }

    /** Forget a board: its key goes, so its packets are no longer accepted. */
    @Synchronized fun removeKey(mac: String) {
        val j = keysJson()
        j.remove(mac.lowercase())
        val tmp = File(filesDir, "device_keys.json.tmp")
        java.io.FileOutputStream(tmp).use { it.write(j.toString().toByteArray()); it.fd.sync() }
        if (!tmp.renameTo(keysFile)) throw java.io.IOException("cannot store the board keys")
        keyCache = keys()
        seen.remove(mac.lowercase())
    }

    /** When any board was last heard (unix seconds), or null if never since the start. */
    fun lastHeard(): Double? = seen.values.maxOfOrNull { it.first }

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
        val seq = hubSeq.incrementAndGet()
        for ((mac, key) in keyCache) {
            try {
                val pkt = Wsn1.encodeHub(mac, seq, key, ip, PORT)
                s.send(DatagramPacket(pkt, pkt.size, InetAddress.getByName("255.255.255.255"), PORT))
                seen[mac]?.let { s.send(DatagramPacket(pkt, pkt.size, it.third)) }  // и напрямую: широковещание теряется
            } catch (e: Exception) { Log.w(TAG, "announce $mac", e) }
        }
    }

    private fun sendCmd(mac: String, cmd: Int, args: ByteArray): Boolean {
        val s = socket ?: return false
        val b = seen[mac] ?: return false
        val key = keyCache[mac] ?: return false
        val pkt = Wsn1.encodeCmd(mac, b.second, cmdSeq.incrementAndGet(), key, cmd, args)
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
        // законченная запись или прерванная (процесс убили посреди обхода: конец — по последней позе)
        val open = synchronized(local) { local.activeDir }
        val dir = sessions().firstOrNull { it != open }
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
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Жучок", NotificationManager.IMPORTANCE_LOW))
        }
        @Suppress("DEPRECATION")
        val b = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) Notification.Builder(this, CHANNEL) else Notification.Builder(this)
        return b.setContentTitle("Жучок на посту")
            .setContentText("Жучок слушает дом")
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
