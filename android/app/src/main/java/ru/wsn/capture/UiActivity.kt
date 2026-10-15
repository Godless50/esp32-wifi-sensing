package ru.wsn.capture

import android.Manifest
import android.annotation.SuppressLint
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.webkit.WebViewAssetLoader
import androidx.webkit.WebViewClientCompat
import org.json.JSONArray
import org.json.JSONObject
import ru.wsn.capture.core.HomeDay
import ru.wsn.capture.esp.Console
import ru.wsn.capture.esp.EspLoader
import ru.wsn.capture.esp.OtgProvision
import ru.wsn.capture.esp.SerialPort
import ru.wsn.capture.esp.UsbSerialPort
import ru.wsn.capture.hub.HubService
import java.security.SecureRandom
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/** «Жучок» для людей: весь интерфейс — локальная веб-страница (assets/ui, дизайн v4) в WebView, а здесь — логика:
 *  прошивка по кабелю и проверка Wi-Fi (OtgProvision), ожидание жучка в сети, минута тишины, обход (нативный
 *  CaptureActivity), обучение в телефоне (HubService) и главный экран. Раз в секунду страница получает полное
 *  состояние (window.app.setState), действия приходят через Android.send(action, json). Контракт — design/v4/CONTRACT.md.
 *  Пароль Wi-Fi проходит только из поля страницы в OtgProvision и в плату; здесь он нигде не хранится. */
class UiActivity : AppCompatActivity() {
    enum class Screen { HELLO, CABLE, CABLE_MISSING, FLASHING, WIFI_ERROR, WIFI_NEW, PLACE, PLACE_ONLINE, PLACE_LATE,
        SILENCE, WALK_INTRO, CAMERA_DENIED, LEARNING, LEARN_FAILED, RESULT, HOME, EVENTS, SETTINGS, MAP }

    private enum class Job { FLASH, RETRY, NEW_WIFI }

    companion object {
        private const val USB_PERMISSION = "ru.wsn.capture.USB_PERMISSION"
        /** A cable job outlives a recreated activity (the board re-enumerates; Android may open the app again). */
        private val flashing = java.util.concurrent.atomic.AtomicBoolean(false)
        @Volatile private var job = Job.FLASH
        @Volatile private var flashText = ""
        @Volatile private var flashProgress = 0
        @Volatile private var flashResult: String? = null     // MAC when the last job finished
        @Volatile private var flashSsid: String? = null       // the network of the last successful job
        @Volatile private var flashError: Pair<String, String>? = null   // kind (auth|not_found|cable|other), text
        /** Flashed and keyed, but not on Wi-Fi: the board is still on the cable, only the network is asked again. */
        @Volatile private var wifiFailMac: String? = null

        /** The board's USB port, asking for permission if needed (the board re-enumerates after flashing). */
        fun openPort(ctx: Context): SerialPort {
            val usb = ctx.getSystemService(Context.USB_SERVICE) as UsbManager
            val end = System.currentTimeMillis() + 20000
            while (System.currentTimeMillis() < end) {
                val dev = usb.deviceList.values.firstOrNull { UsbSerialPort.matches(it) }
                if (dev == null) { Thread.sleep(300); continue }
                if (!usb.hasPermission(dev) && !askUsb(ctx, usb, dev)) throw EspLoader.EspError("нет разрешения на USB")
                return UsbSerialPort.open(usb, dev)
            }
            throw EspLoader.EspError("жучок не найден по кабелю")
        }

        private fun askUsb(ctx: Context, usb: UsbManager, dev: UsbDevice): Boolean {
            val latch = CountDownLatch(1)
            var granted = false
            val rx = object : BroadcastReceiver() {
                override fun onReceive(c: Context, i: Intent) { granted = i.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false); latch.countDown() }
            }
            val filter = IntentFilter(USB_PERMISSION)
            if (Build.VERSION.SDK_INT >= 33) ctx.registerReceiver(rx, filter, Context.RECEIVER_NOT_EXPORTED) else ctx.registerReceiver(rx, filter)
            val flags = if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0
            usb.requestPermission(dev, PendingIntent.getBroadcast(ctx, 0, Intent(USB_PERMISSION).setPackage(ctx.packageName), flags))
            latch.await(60, TimeUnit.SECONDS)
            try { ctx.unregisterReceiver(rx) } catch (_: Exception) {}
            return granted
        }
    }

    private val main = Handler(Looper.getMainLooper())
    private val prefs by lazy { getSharedPreferences("wsn-ui", MODE_PRIVATE) }
    private lateinit var web: WebView
    private var ready = false
    private var screen = Screen.HELLO
    private var error: Pair<String, String>? = null
    private var showSsid = false
    private var lastBoard: String? = null
    private var placeSince = 0L
    private var surveyStarted = 0L
    private var surveyDoneAt = 0L
    private var surveyed: Set<String> = emptySet()   // жучки, которым опрос действительно ушёл
    private var walkBefore: String? = null      // newest session before the walk was started
    private var walkOpen = false

    // ======================= lifecycle =======================
    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        HubService.start(this)
        if (Build.VERSION.SDK_INT >= 33 && !notificationsOk())
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 8)
        screen = initialScreen()
        if (flashing.get()) screen = Screen.FLASHING
        placeSince = System.currentTimeMillis()   // восстановили «Поставь» — две минуты отсчитываем заново
        walkBefore = prefs.getString("walk_before", null)
        walkOpen = prefs.getBoolean("walk_open", false)
        lastBoard = prefs.getString("last_board", null)

        val loader = WebViewAssetLoader.Builder().addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this)).build()
        web = WebView(this).apply {
            setBackgroundColor(0xFFFFF6E6.toInt())
            settings.javaScriptEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.domStorageEnabled = false
            settings.textZoom = 100                       // макет рассчитан на 1:1; крупный шрифт системы ломает игрушку
            overScrollMode = WebView.OVER_SCROLL_NEVER
            // пароль Wi-Fi уходит только в жучок: службы автозаполнения его не видят и не предлагают сохранить
            if (Build.VERSION.SDK_INT >= 26) importantForAutofill = android.view.View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS
            webViewClient = object : WebViewClientCompat() {
                override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
                    loader.shouldInterceptRequest(request.url)
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean = true  // только наша страница
                override fun onPageFinished(view: WebView, url: String) {
                    ready = true
                    applyTop()
                    push()
                }
            }
            addJavascriptInterface(Bridge(), "Android")
        }
        // Android 15 рисует под системными панелями: сверху — отступ страницы (--top), снизу — панель/клавиатура
        val root = android.widget.FrameLayout(this).apply { setBackgroundColor(0xFFFFF6E6.toInt()); addView(web) }
        androidx.core.view.ViewCompat.setOnApplyWindowInsetsListener(root) { v, insets ->
            val bars = insets.getInsets(androidx.core.view.WindowInsetsCompat.Type.systemBars() or
                androidx.core.view.WindowInsetsCompat.Type.displayCutout())
            val ime = insets.getInsets(androidx.core.view.WindowInsetsCompat.Type.ime())
            topPx = bars.top
            v.setPadding(bars.left, 0, bars.right, maxOf(bars.bottom, ime.bottom))
            applyTop()
            androidx.core.view.WindowInsetsCompat.CONSUMED   // иначе страница добавит отступ панели ещё раз
        }
        setContentView(root)
        web.loadUrl("https://appassets.androidplatform.net/assets/ui/index.html")
        main.post(object : Runnable { override fun run() { tick(); push(); main.postDelayed(this, 1000) } })
    }

    override fun onResume() {
        super.onResume()
        if (walkOpen) afterWalk()
        push()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        // жучок подключили кабелем — сразу к заданию «Подключи», если это не середина работы
        if (intent.action == UsbManager.ACTION_USB_DEVICE_ATTACHED && !flashing.get() &&
            screen in setOf(Screen.HELLO, Screen.CABLE_MISSING)) go(Screen.CABLE)
    }

    override fun onDestroy() { ready = false; main.removeCallbacksAndMessages(null); web.destroy(); super.onDestroy() }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() { back() }

    private fun hub() = HubService.current
    private fun hasModel() = java.io.File(filesDir, "model/model.json").exists()

    private fun initialScreen(): Screen {
        val saved = prefs.getString("screen", null)?.let { runCatching { Screen.valueOf(it) }.getOrNull() }
        return when {
            saved != null -> saved
            hasModel() -> Screen.HOME
            else -> Screen.HELLO
        }
    }

    private fun go(s: Screen) {
        if (s == Screen.PLACE && screen != Screen.PLACE && screen != Screen.PLACE_LATE) placeSince = System.currentTimeMillis()
        if (s == Screen.SILENCE && screen != Screen.SILENCE) startSurvey()
        if (s != Screen.WIFI_ERROR) showSsid = false
        screen = s
        // запоминаем шаг настройки; временные экраны откроются с их «родителя»
        val keep = when (s) {
            Screen.FLASHING, Screen.CABLE_MISSING, Screen.WIFI_ERROR -> Screen.CABLE
            Screen.WIFI_NEW, Screen.EVENTS, Screen.SETTINGS, Screen.MAP -> Screen.HOME
            Screen.PLACE_LATE, Screen.PLACE_ONLINE -> Screen.PLACE
            Screen.CAMERA_DENIED -> Screen.WALK_INTRO
            Screen.LEARN_FAILED -> Screen.WALK_INTRO
            else -> s
        }
        prefs.edit().putString("screen", keep.name).apply()
        push()
    }

    private fun back() {
        when (screen) {
            Screen.HELLO, Screen.HOME -> moveTaskToBack(true)
            Screen.FLASHING, Screen.LEARNING -> moveTaskToBack(true)        // идёт работа — не прерываем
            Screen.EVENTS, Screen.SETTINGS, Screen.WIFI_NEW, Screen.RESULT, Screen.MAP -> go(Screen.HOME)
            Screen.CABLE, Screen.CABLE_MISSING, Screen.WIFI_ERROR -> go(if (hasModel()) Screen.HOME else Screen.HELLO)
            Screen.PLACE, Screen.PLACE_ONLINE, Screen.PLACE_LATE -> go(if (hasModel()) Screen.HOME else Screen.CABLE)
            Screen.SILENCE -> go(Screen.PLACE_ONLINE)
            Screen.WALK_INTRO -> go(if (hasModel()) Screen.HOME else Screen.SILENCE)
            Screen.CAMERA_DENIED, Screen.LEARN_FAILED -> go(Screen.WALK_INTRO)
        }
    }

    // ======================= actions from the page =======================
    private inner class Bridge {
        @JavascriptInterface fun send(action: String, json: String) {
            val args = try { JSONObject(json) } catch (e: Exception) { JSONObject() }
            main.post { if (!isFinishing && !isDestroyed) onAction(action, args) }
        }
    }

    private fun onAction(a: String, args: JSONObject) {
        when (a) {
            "start" -> go(Screen.CABLE)
            "flash" -> startFlash(args.optString("ssid").trim(), args.optString("password"))
            "retryWifi" -> if (wifiFailMac == null) startFlash(args.optString("ssid").trim(), args.optString("password"))  // ключа в плате ещё нет
                else startRetry(args.optString("ssid").trim(), args.optString("password"), Job.RETRY)
            "newWifi" -> if (args.has("ssid")) startRetry(args.optString("ssid").trim(), args.optString("password"), Job.NEW_WIFI) else { error = null; go(Screen.WIFI_NEW) }
            "cableRetry", "reflash" -> { wifiFailMac = null; error = null; go(Screen.CABLE) }
            "otherNetwork" -> if (screen == Screen.WIFI_ERROR) { showSsid = true; push() }
            "placeNext" -> go(Screen.SILENCE)
            "placeRecheck" -> { hub()?.announce(); placeSince = System.currentTimeMillis(); if (screen == Screen.PLACE_LATE) go(Screen.PLACE) }
            "surveyStart", "surveyRestart" -> { startSurvey(); push() }
            "surveySkip" -> go(Screen.WALK_INTRO)
            "walkStart" -> startWalk()
            "walkLater" -> if (hasModel()) go(Screen.HOME) else moveTaskToBack(true)
            "learnRetry", "rewalk" -> go(Screen.WALK_INTRO)
            "openHome" -> go(Screen.HOME)
            "openEvents" -> go(Screen.EVENTS)
            "openMap" -> go(Screen.MAP)
            "openSettings" -> go(Screen.SETTINGS)
            "back" -> back()
            "addBoard" -> { wifiFailMac = null; error = null; go(Screen.CABLE) }
            "removeBoard" -> confirmRemove(args.optString("mac"))
            "computerMode" -> startActivity(Intent(this, HomeActivity::class.java).putExtra(HomeActivity.EXTRA_CLASSIC, true))
            "askNotifications" -> if (Build.VERSION.SDK_INT >= 33) ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 8)
            "askBattery" -> runCatching {
                startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
            }
            "askCamera" -> askCamera()
        }
    }

    private fun confirmRemove(mac: String) {
        val h = hub() ?: return
        if (mac.isEmpty() || isFinishing || isDestroyed) return
        AlertDialog.Builder(this).setMessage("Убрать «${h.boardName(mac)}»? Чтобы вернуть, жучок снова подключают кабелем.")
            .setPositiveButton("Убрать") { _, _ -> runCatching { h.removeKey(mac) }; push() }
            .setNegativeButton("Оставить", null).show()
    }

    // ======================= the cable =======================
    private fun startFlash(ssid: String, password: String) {
        if (ssid.isEmpty()) return
        val h = hub() ?: return
        val host = h.localIp() ?: run { error = "other" to "Телефон не в домашнем Wi-Fi"; go(Screen.CABLE_MISSING); return }
        val images = try { listOf("main", "lite").associateWith { assets.open("firmware/wsn-$it.bin").use { s -> s.readBytes() } } }
            catch (e: java.io.IOException) { error = "other" to "В приложении нет программы для жучка"; go(Screen.CABLE_MISSING); return }
        if (!beginJob(Job.FLASH, "Связь с жучком…")) return
        val key = ByteArray(32).also { SecureRandom().nextBytes(it) }.joinToString("") { "%02x".format(it.toInt() and 0xFF) }
        val app = applicationContext
        thread {
            try {
                val n = h.knownBoards().size + 1
                val r = OtgProvision({ openPort(app) }, images) { flashText = it }
                    .run(key, OtgProvision.Settings(ssid, password, "", host, HubService.PORT),
                        onProgress = { p -> flashProgress = p },
                        beforeProvision = { mac ->
                            val m = mac.lowercase()   // известный жучок (перепрошивка) сохраняет своё имя
                            h.addKey(mac, key, if (m in h.knownBoards()) h.boardName(m) else if (n == 1) "Жучок" else "Жучок $n")
                        })
                h.announce()
                flashSsid = ssid
                flashResult = r.mac.lowercase()
            } catch (e: OtgProvision.WifiFailed) {
                h.announce()                       // ключ уже в плате и в телефоне; не хватает только сети
                wifiFailMac = e.mac.lowercase()
                flashError = (if (e.outcome == Console.Wifi.NOT_FOUND) "not_found" else if (e.outcome == Console.Wifi.AUTH_FAIL) "auth" else "other") to (e.message ?: "")
            } catch (e: OtgProvision.NetworkNotSeen) {
                flashError = "not_found" to (e.message ?: "")
            } catch (e: Exception) {
                flashError = cableError(e)
            } finally {
                flashing.set(false)
            }
        }
    }

    private fun startRetry(ssid: String, password: String, kind: Job) {
        if (ssid.isEmpty() || hub() == null) return
        if (!beginJob(kind, "Проверяем Wi-Fi…")) return
        val app = applicationContext
        thread {
            try {
                val r = OtgProvision({ openPort(app) }, emptyMap()) { flashText = it }
                    .retryWifi(OtgProvision.Settings(ssid, password, "", "", HubService.PORT))
                wifiFailMac = null
                flashSsid = ssid
                flashResult = r.mac.lowercase()
            } catch (e: OtgProvision.WifiFailed) {
                if (kind == Job.NEW_WIFI) wifiFailMac = e.mac.lowercase()
                flashError = (if (e.outcome == Console.Wifi.NOT_FOUND) "not_found" else if (e.outcome == Console.Wifi.AUTH_FAIL) "auth" else "other") to (e.message ?: "")
            } catch (e: OtgProvision.NetworkNotSeen) {
                flashError = "not_found" to (e.message ?: "")
            } catch (e: Exception) {
                flashError = cableError(e)
            } finally {
                flashing.set(false)
            }
        }
    }

    private fun cableError(e: Exception): Pair<String, String> =
        if (e.message?.contains("разрешения на USB") == true) "other" to "Разреши доступ к USB" else "cable" to (e.message ?: e.javaClass.simpleName)

    private fun beginJob(kind: Job, text: String): Boolean {
        if (!flashing.compareAndSet(false, true)) { go(Screen.FLASHING); return false }
        job = kind
        flashText = text; flashProgress = 0; flashResult = null; flashError = null; error = null
        go(Screen.FLASHING)
        return true
    }

    // ======================= quiet minute, walk, training =======================
    private fun startSurvey() {
        val h = hub() ?: return
        val live = h.boards().map { it.mac }.toSet()
        h.knownBoards().forEach { h.surveys.remove(it) }      // прошлый опрос не должен сразу «закончить» минуту
        surveyed = h.knownBoards().filter { it in live && h.survey(it) }.toSet()
        surveyStarted = System.currentTimeMillis(); surveyDoneAt = 0
    }

    private fun startWalk() {
        walkBefore = hub()?.sessions()?.firstOrNull()?.name
        walkOpen = true
        prefs.edit().putString("walk_before", walkBefore).putBoolean("walk_open", true).putBoolean("walk_complete", false).apply()
        startActivity(Intent(this, CaptureActivity::class.java))
    }

    /** Back from the walk: no camera — say so; a finished walk — train on it (a dropped one stays on disk, not trained). */
    private fun afterWalk() {
        val h = hub() ?: return
        walkOpen = false
        prefs.edit().putBoolean("walk_open", false).apply()
        val newest = h.sessions().firstOrNull()?.name
        when {
            newest != null && newest != walkBefore && prefs.getBoolean("walk_complete", false) -> { h.trainNewest(); go(Screen.LEARNING) }
            ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED -> go(Screen.CAMERA_DENIED)
            else -> go(Screen.WALK_INTRO)
        }
    }

    private fun askCamera() {
        if (ActivityCompat.shouldShowRequestPermissionRationale(this, Manifest.permission.CAMERA) ||
            !prefs.getBoolean("camera_asked", false)) {
            prefs.edit().putBoolean("camera_asked", true).apply()
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.CAMERA), 9)
        } else {   // «больше не спрашивать» — только через настройки приложения
            startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 9 && grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) go(Screen.WALK_INTRO)
        push()
    }

    // ======================= the state machine, once a second =======================
    private fun tick() {
        val h = hub()
        when (screen) {
            Screen.FLASHING -> {
                flashError?.let { (kind, text) ->
                    flashError = null
                    error = kind to text
                    when {
                        job == Job.NEW_WIFI -> go(Screen.WIFI_NEW)
                        kind == "cable" || kind == "other" && wifiFailMac == null -> go(Screen.CABLE_MISSING)
                        else -> go(Screen.WIFI_ERROR)
                    }
                    return
                }
                flashResult?.let { mac ->
                    flashResult = null; error = null
                    flashSsid?.let { h?.homeSsid = it }
                    if (job == Job.NEW_WIFI) { go(Screen.HOME); return }
                    lastBoard = mac; prefs.edit().putString("last_board", mac).apply()
                    go(Screen.PLACE)
                    return
                }
                if (!flashing.get() && flashResult == null && flashError == null) go(Screen.CABLE)   // работа пропала (процесс перезапущен)
            }
            Screen.PLACE, Screen.PLACE_LATE -> {
                if (h == null) return
                val targets = lastBoard?.let { listOf(it) } ?: h.knownBoards()
                val live = h.boards().map { it.mac }.toSet()
                if (targets.any { it in live }) { go(Screen.PLACE_ONLINE); return }
                if (screen == Screen.PLACE && System.currentTimeMillis() - placeSince > 120_000) go(Screen.PLACE_LATE)
            }
            Screen.SILENCE -> {
                if (h == null) return
                if (surveyStarted == 0L) startSurvey()
                val done = surveyed.isNotEmpty() && surveyed.all { (h.surveys[it]?.state ?: 0) >= 3 } ||
                    System.currentTimeMillis() - surveyStarted > (if (surveyed.isEmpty()) 60_000 else 80_000)
                if (done && surveyDoneAt == 0L) surveyDoneAt = System.currentTimeMillis()
                if (surveyDoneAt > 0 && System.currentTimeMillis() - surveyDoneAt > 2500) go(Screen.WALK_INTRO)
            }
            Screen.LEARNING -> {
                val t = h?.train ?: return
                if (!t.running && t.ok == true) go(Screen.RESULT)
                else if (!t.running && t.ok == false) go(Screen.LEARN_FAILED)
                else if (!t.running && t.ok == null) go(if (hasModel()) Screen.RESULT else Screen.WALK_INTRO)   // приложение перезапускали
            }
            else -> {}
        }
    }

    private var topPx = 0

    private fun applyTop() {
        if (!ready) return
        val css = (topPx / resources.displayMetrics.density).toInt() + 16
        web.evaluateJavascript("document.documentElement.style.setProperty('--top','${css}px')", null)
    }

    private fun push() {
        if (!ready) return
        val json = state().toString().replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
        web.evaluateJavascript("window.app && window.app.setState($json)", null)
    }

    private fun notificationsOk() = Build.VERSION.SDK_INT < 33 ||
        ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    private fun state(): JSONObject {
        val h = hub()
        val o = JSONObject().put("screen", screen.name.lowercase())
        o.put("version", runCatching { packageManager.getPackageInfo(packageName, 0).versionName }.getOrNull() ?: "1.0")
        o.put("ssid", h?.currentSsid() ?: h?.homeSsid ?: "")
        val step = when (screen) {
            Screen.CABLE, Screen.CABLE_MISSING, Screen.FLASHING, Screen.WIFI_ERROR -> 1
            Screen.PLACE, Screen.PLACE_ONLINE, Screen.PLACE_LATE -> 2
            Screen.SILENCE -> 3
            Screen.WALK_INTRO, Screen.CAMERA_DENIED -> 4
            Screen.LEARNING, Screen.LEARN_FAILED -> 5
            else -> 0
        }
        if (step > 0) o.put("step", JSONObject().put("n", step))
        error?.let { (k, t) -> o.put("error", JSONObject().put("kind", if (showSsid && k == "auth") "not_found" else k).put("text", t)) }
        if (screen == Screen.WIFI_ERROR && showSsid && error == null) o.put("error", JSONObject().put("kind", "not_found"))
        o.put("flash", JSONObject()
            .put("stage", when { job != Job.FLASH || flashText.contains("Wi-Fi") -> 2; flashProgress > 0 -> 1; else -> 0 })
            .put("progress", flashProgress))
        if (h != null) {
            o.put("boards", JSONArray().apply {
                val live = h.boards(h.offlineAfterS).map { it.mac }.toSet()
                for (mac in h.knownBoards()) put(JSONObject().put("mac", mac).put("name", h.boardName(mac)).put("online", mac in live))
            })
            if (screen == Screen.SILENCE) {
                val left = 60 - ((System.currentTimeMillis() - surveyStarted) / 1000).toInt()
                val helpers = h.knownBoards().sumOf { mac -> h.surveys[mac]?.entries?.count { it.flags and 1 != 0 } ?: 0 }
                o.put("survey", JSONObject().put("running", surveyDoneAt == 0L).put("secondsLeft", left.coerceAtLeast(0))
                    .put("helpers", helpers).put("done", surveyDoneAt > 0))
            }
            val t = h.train
            o.put("train", JSONObject().put("running", t.running).put("progress", t.progress).put("failed", t.ok == false).put("message", t.message))
            val meta = h.modelMeta()
            if (screen == Screen.RESULT && meta != null) {
                val held = meta.optJSONObject("metrics")?.optJSONObject("heldout")
                val rooms = if (meta.optBoolean("rooms_ok")) roomNames(meta) else null
                o.put("result", JSONObject()
                    .put("presence", held?.optDouble("nobody_home_bal_acc", 0.0) ?: 0.0)   // нет метрики — не «проверено»
                    .put("motion", held?.optDouble("moving_bal_acc", 0.0) ?: 0.0)
                    .put("rooms", rooms?.let { JSONArray(it) } ?: JSONObject.NULL))
            }
            if (screen == Screen.HOME || screen == Screen.EVENTS || screen == Screen.MAP) o.put("home", homeState(h, meta))
        }
        o.put("checklist", JSONObject().put("notifications", notificationsOk())
            .put("battery", (getSystemService(Context.POWER_SERVICE) as PowerManager).isIgnoringBatteryOptimizations(packageName)))
        return o
    }

    private fun roomNames(meta: JSONObject): List<String> {
        val rooms = meta.optJSONArray("rooms") ?: return emptyList()
        return (0 until rooms.length()).mapNotNull { rooms.optJSONObject(it)?.optString("name")?.takeIf { n -> n.isNotBlank() } }
    }

    private fun homeState(h: HubService, meta: JSONObject?): JSONObject {
        val now = h.now()
        val cal = Calendar.getInstance().apply { set(Calendar.HOUR_OF_DAY, 0); set(Calendar.MINUTE, 0); set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0) }
        val dayStart = cal.timeInMillis / 1000.0
        val fmt = SimpleDateFormat("HH:mm", Locale("ru"))
        val hm = { t: Double -> fmt.format(Date((t * 1000).toLong())) }
        val v = h.verdict
        val roomsOk = meta?.optBoolean("rooms_ok") == true
        val live = h.boards(h.offlineAfterS)
        val events = h.events.events()
        val state = when {
            meta == null -> "hark"
            h.isAway() -> "away"
            live.isEmpty() && h.lastHeard() == null && now - h.startedAt < h.offlineAfterS -> "hark"
            live.isEmpty() -> "offline"
            v == null -> "hark"
            v.emptyP >= 0.5 -> "empty"
            v.movingP >= 0.5 -> "moving"
            else -> "quiet"
        }
        val o = JSONObject().put("state", state).put("now", (now - dayStart) / 3600.0)
        if (roomsOk) {
            o.put("rooms", JSONArray(roomNames(meta!!)))
            // комната — та, где точка (она уже очищена от скачков); без точки — что сказала модель
            if (state == "moving" || state == "quiet") v?.zone?.let { o.put("zone", it) }
        }
        when (state) {
            "offline" -> (h.lastHeard() ?: events.firstOrNull { it.state == ru.wsn.capture.core.EventLog.State.OFFLINE }?.t)?.let { o.put("since", hm(it)) }
            "away" -> events.firstOrNull { it.state == ru.wsn.capture.core.EventLog.State.NODATA }?.t?.let { o.put("since", hm(it)) }
        }
        // план и человек на нём: комнаты (с поправленными стенами), жучки, точка без скачков
        val plan = h.planRooms()
        if (plan.isNotEmpty()) {
            val boardsXy = JSONArray()
            meta?.optJSONObject("boards")?.let { b -> for (k in b.keys()) b.optJSONArray(k)?.let { boardsXy.put(it) } }
            o.put("plan", JSONObject().put("boards", boardsXy).put("rooms", JSONArray().apply {
                for (r in plan) put(JSONObject().put("name", r.name).put("poly", JSONArray().apply { for (p in r.poly) put(JSONArray().put(p[0]).put(p[1])) }))
            }))
            val d = h.dot
            if (d != null && (state == "moving" || state == "quiet")) {
                o.put("dot", JSONObject().put("x", d.x).put("y", d.y).put("r", d.r))
                if (!h.placeError75().isNaN()) o.put("err", h.placeError75())
            }
        }
        val (segs, items) = HomeDay.build(events, dayStart, now)
        o.put("day", JSONArray().apply { for (s in segs) put(JSONArray().put(s.from).put(s.to).put(s.state)) })
        o.put("events", JSONArray().apply {
            for (e in items) put(JSONObject().put("kind", e.kind).put("t", hm(e.t)).put("t2", e.t2?.let(hm) ?: JSONObject.NULL)
                .put("zone", e.zone ?: JSONObject.NULL))
        })
        return o
    }
}
