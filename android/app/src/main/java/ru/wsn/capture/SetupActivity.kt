package ru.wsn.capture

import android.Manifest
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import ru.wsn.capture.esp.EspLoader
import ru.wsn.capture.esp.OtgProvision
import ru.wsn.capture.esp.SerialPort
import ru.wsn.capture.esp.UsbSerialPort
import ru.wsn.capture.hub.HubService
import java.security.SecureRandom
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/** Setup without a computer, for people who never flashed anything: the phone becomes the hub.
 *  1) the board by cable: the phone flashes it and gives it the Wi-Fi (the password goes over the cable into the
 *     board only); 2) put the board where it will stay — it comes back over Wi-Fi; 3) more boards or go on;
 *  4) quiet mode: each board finds the home devices it hears (about a minute, nobody moving);
 *  5) the walk with the phone (AR rooms + scenario); 6) training on the phone; 7) the map. */
class SetupActivity : AppCompatActivity() {
    private enum class Page { INTRO, CABLE, PLACE, MORE, SURVEY, WALK, TRAIN, DONE }

    private val main = Handler(Looper.getMainLooper())
    private lateinit var title: TextView
    private lateinit var body: TextView
    private lateinit var progress: ProgressBar
    private lateinit var form: LinearLayout
    private lateinit var buttons: LinearLayout
    private var page = Page.INTRO
    private var busy = false
    private var lastBoard: String? = null
    private var surveyStarted = 0L
    private val prefs by lazy { getSharedPreferences("wsn-setup", MODE_PRIVATE) }

    companion object {
        private const val USB_PERMISSION = "ru.wsn.capture.USB_PERMISSION"
        /** A flash is running (it outlives a recreated activity: the board re-enumerates and the phone may offer to
         *  open the app again). A second one must not start on the same USB device. */
        private val flashing = java.util.concurrent.atomic.AtomicBoolean(false)
        @Volatile private var flashText = ""
        @Volatile private var flashProgress = 0
        @Volatile private var flashResult: String? = null   // MAC of the board when the last flash finished
        @Volatile private var flashError: String? = null
        /** The board flashed and keyed, but it did not join Wi-Fi: it is still on the cable, ask the password again. */
        @Volatile private var wifiFailMac: String? = null
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        HubService.start(this)
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 8)  // значок работы центра
        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(44, 72, 44, 44) }
        title = TextView(this).apply { textSize = 24f; setTextColor(Color.WHITE) }
        body = TextView(this).apply { textSize = 16f; setTextColor(Color.rgb(209, 213, 219)); setPadding(0, 28, 0, 28) }
        progress = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply { max = 100; visibility = android.view.View.GONE }
        form = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        buttons = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(0, 24, 0, 0) }
        listOf(title, body, progress, form, buttons).forEach { col.addView(it) }
        setContentView(ScrollView(this).apply { setBackgroundColor(Color.rgb(11, 13, 17)); addView(col) })
        page = try { Page.valueOf(prefs.getString("page", "INTRO")!!) } catch (e: Exception) { Page.INTRO }
        if (page == Page.TRAIN && hub()?.train?.running != true) page = Page.WALK
        if (page == Page.CABLE && flashing.get()) busy = true   // прошивка идёт в другом экземпляре экрана
        show()
        main.post(object : Runnable { override fun run() { refresh(); main.postDelayed(this, 1000) } })
    }

    override fun onResume() { super.onResume(); if (page == Page.WALK) show() }

    override fun onNewIntent(intent: Intent) { super.onNewIntent(intent) }  // плата переподключилась: экран тот же

    override fun onDestroy() { main.removeCallbacksAndMessages(null); super.onDestroy() }

    private fun go(p: Page) { page = p; prefs.edit().putString("page", p.name).apply(); show() }

    private fun button(label: String, primary: Boolean = true, onClick: () -> Unit) {
        buttons.addView(Button(this).apply {
            text = label; isAllCaps = false; textSize = 16f; setTextColor(Color.WHITE)
            background = GradientDrawable().apply { cornerRadius = 28f; setColor(if (primary) Color.rgb(59, 130, 246) else Color.argb(60, 255, 255, 255)) }
            setOnClickListener { if (!busy) onClick() }
        }, LinearLayout.LayoutParams(MATCH_PARENT, 150).apply { setMargins(0, 10, 0, 10) })
    }

    private fun hub() = HubService.current

    private fun show() {
        form.removeAllViews(); buttons.removeAllViews(); progress.visibility = android.view.View.GONE
        when (page) {
            Page.INTRO -> {
                title.text = "Настройка"
                body.text = "Нужно: плата ESP32-S3, кабель USB-C (OTG) и домашний Wi-Fi 2,4 ГГц.\n\n" +
                    "Телефон сам прошьёт плату и научит её вашему Wi-Fi. Потом этот телефон остаётся дома " +
                    "и работает центром: принимает данные плат и показывает, где кто-то есть."
                button("Подключить плату кабелем") { go(Page.CABLE) }
                if (hub()?.knownBoards()?.isNotEmpty() == true) button("Платы уже настроены — дальше", false) { go(Page.MORE) }
            }
            Page.CABLE -> if (wifiFailMac != null && !busy) wifiRetryPage(null) else cablePage()
            Page.PLACE -> {
                title.text = "Поставьте плату на место"
                body.text = "Плата подключилась к вашему Wi-Fi. Отключите кабель и поставьте её туда, где она будет " +
                    "стоять всегда, — в розетку через зарядку. Лучше повыше, не в шкаф и не за телевизор. " +
                    "Ждём, когда она выйдет на связь…"
            }
            Page.MORE -> {
                title.text = "Платы"
                body.text = boardsText()
                button("Добавить ещё плату", false) { go(Page.CABLE) }
                button("Дальше: режим покоя") { go(Page.SURVEY) }
            }
            Page.SURVEY -> {
                title.text = "Режим покоя"
                body.text = "Около минуты каждая плата ищет устройства вашего Wi-Fi (розетки, телевизор, холодильник…) " +
                    "и выбирает те, которые хорошо слышит: так у неё становится больше линий. В это время " +
                    "лучше никому не ходить по квартире."
                button("Начать") { startSurvey() }
                button("Пропустить", false) { go(Page.WALK) }
            }
            Page.WALK -> {
                title.text = "Обход квартиры"
                val last = hub()?.sessions()?.firstOrNull()
                body.text = "Телефон покажет, что делать: отметить платы и углы комнат камерой, потом пройти по " +
                    "комнатам по подсказкам (около 20 минут). На шагах «выйдите из квартиры» оставьте телефон дома." +
                    (if (last != null) "\n\nПоследняя запись: ${last.name}" else "")
                button("Начать обход") { startActivity(Intent(this, CaptureActivity::class.java)) }
                if (last != null) button("Обход записан — обучить", false) { go(Page.TRAIN); hub()?.trainNewest() }
            }
            Page.TRAIN -> {
                title.text = "Обучение"
                body.text = "Телефон учится на вашей записи. Это несколько минут; экран можно не трогать."
                progress.visibility = android.view.View.VISIBLE
            }
            Page.DONE -> {
                title.text = "Готово"
                body.text = (hub()?.train?.message ?: "") + "\n\nОставьте телефон дома на зарядке: он центр системы."
                button("Открыть карту") { startActivity(Intent(this, HubMapActivity::class.java)) }
                val pm = getSystemService(Context.POWER_SERVICE) as android.os.PowerManager
                if (!pm.isIgnoringBatteryOptimizations(packageName)) button("Не усыплять центр", false) {
                    // без этого система может приостановить приём, пока телефон не на зарядке
                    startActivity(Intent(android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                        android.net.Uri.parse("package:$packageName")))
                }
                button("Пройти обход заново", false) { go(Page.WALK) }
            }
        }
    }

    private fun boardsText(): String {
        val h = hub() ?: return "Центр запускается…"
        val known = h.knownBoards()
        val live = h.boards().associateBy { it.mac }
        if (known.isEmpty()) return "Плат пока нет."
        return "Плат: ${known.size}" + known.joinToString("") { mac ->
            "\n  • ${h.boardName(mac)} — " + if (live.containsKey(mac)) "на связи" else "нет связи"
        } + if (known.size == 1) "\n\nС одной платой будет видно «кто-то дома / никого, движется / тихо». " +
            "Комнаты — если проверка покажет, что плата их различает; вторая плата помогает." else ""
    }

    private fun refresh() {
        val h = hub() ?: return
        when (page) {
            Page.CABLE -> if (busy) {
                progress.visibility = android.view.View.VISIBLE
                progress.progress = flashProgress; body.text = flashText
                flashError?.let { busy = false; flashError = null; progress.visibility = android.view.View.GONE
                    if (wifiFailMac != null) wifiRetryPage(it)
                    else body.text = "Не получилось: $it\n\nПроверьте кабель (нужен кабель с данными) и попробуйте ещё раз." }
                flashResult?.let { lastBoard = it; flashResult = null; busy = false; go(Page.PLACE) }
            }
            Page.PLACE -> {
                val mac = lastBoard
                if (mac != null && h.boards().any { it.mac == mac }) { body.text = "Плата на связи."; main.postDelayed({ go(Page.MORE) }, 800) }
            }
            Page.MORE -> body.text = boardsText()
            Page.SURVEY -> if (surveyStarted > 0) {
                val boards = h.knownBoards()
                val lines = boards.map { mac ->
                    val s = h.surveys[mac]
                    val name = h.boardName(mac)
                    when {
                        s == null -> "$name: ждём ответа…"
                        s.state == 3 -> "$name: устройств в сети ${s.responders}, новых линий ${s.entries.count { it.flags and 1 != 0 }}"
                        s.state == 4 -> "$name: не получилось"
                        else -> "$name: ${s.progress}%"
                    }
                }
                body.text = lines.joinToString("\n")
                val done = boards.all { (h.surveys[it]?.state ?: 0) >= 3 }
                if (done || System.currentTimeMillis() - surveyStarted > 150_000) {
                    surveyStarted = 0; buttons.removeAllViews(); button("Дальше: обход") { go(Page.WALK) }
                }
            }
            Page.TRAIN -> {
                val t = h.train
                progress.progress = (t.progress * 100).toInt()
                if (t.running) body.text = "Телефон учится на вашей записи: ${t.stage}…"
                else if (t.ok == true) go(Page.DONE)
                else if (t.ok == false) { body.text = t.message; buttons.removeAllViews(); button("Пройти обход заново") { go(Page.WALK) } }
            }
            else -> {}
        }
    }

    private fun startSurvey() {
        val h = hub() ?: return
        buttons.removeAllViews()
        val live = h.boards().map { it.mac }.toSet()
        val sent = h.knownBoards().filter { it in live }.count { h.survey(it) }
        if (sent == 0) { body.text = "Ни одна плата сейчас не на связи. Проверьте питание плат."; button("Ещё раз") { startSurvey() }; return }
        surveyStarted = System.currentTimeMillis()
    }

    // ---------------- the board by cable ----------------
    private fun cablePage() {
        title.text = "Плата по кабелю"
        body.text = "Подключите плату к телефону кабелем USB-C. Введите имя и пароль домашнего Wi-Fi: пароль уйдёт по " +
            "кабелю только в плату и в телефоне не сохранится."
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED)
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.ACCESS_FINE_LOCATION), 7)  // имя сети Wi-Fi
        val ssid = EditText(this).apply { hint = "Имя сети Wi-Fi"; setText(currentSsid() ?: ""); setTextColor(Color.WHITE); setHintTextColor(Color.GRAY) }
        val pass = EditText(this).apply {
            hint = "Пароль Wi-Fi"; inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            setTextColor(Color.WHITE); setHintTextColor(Color.GRAY)
        }
        form.addView(ssid); form.addView(pass)
        button("Прошить и настроить") {
            val s = ssid.text.toString().trim(); val pw = pass.text.toString()
            if (s.isEmpty()) { body.text = "Введите имя сети."; return@button }
            if (pw.isNotEmpty() && (pw.length < 8 || pw.length > 63)) { body.text = "Пароль Wi-Fi — от 8 до 63 символов."; return@button }
            pass.setText("")  // пароль не держим в поле дольше нужного
            flash(s, pw)
        }
        button("Назад", false) { go(Page.INTRO) }
    }

    @Suppress("DEPRECATION")
    private fun currentSsid(): String? {
        val wifi = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        val s = wifi.connectionInfo?.ssid ?: return null
        return s.removeSurrounding("\"").takeIf { it.isNotEmpty() && it != "<unknown ssid>" }
    }

    /** Open the board's USB port, asking for permission if needed (the board re-enumerates after flashing). */
    private fun openPort(ctx: Context): SerialPort {
        val usb = ctx.getSystemService(Context.USB_SERVICE) as UsbManager
        val end = System.currentTimeMillis() + 20000
        while (System.currentTimeMillis() < end) {
            val dev = usb.deviceList.values.firstOrNull { UsbSerialPort.matches(it) }
            if (dev == null) { Thread.sleep(300); continue }
            if (!usb.hasPermission(dev) && !askPermission(ctx, usb, dev)) throw EspLoader.EspError("нет разрешения на USB")
            return UsbSerialPort.open(usb, dev)
        }
        throw EspLoader.EspError("плата не подключена (нужен кабель с данными и OTG)")
    }

    private fun askPermission(ctx: Context, usb: UsbManager, dev: UsbDevice): Boolean {
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

    private fun flash(ssid: String, password: String) {
        val h = hub() ?: run { body.text = "Центр запускается, подождите секунду."; return }
        val host = h.localIp() ?: run { body.text = "Телефон не подключён к Wi-Fi. Подключите его к той же сети."; return }
        if (!flashing.compareAndSet(false, true)) { busy = true; return }
        busy = true
        flashText = "Связь с платой…"; flashProgress = 0; flashResult = null; flashError = null
        progress.visibility = android.view.View.VISIBLE; progress.progress = 0
        val key = ByteArray(32).also { SecureRandom().nextBytes(it) }.joinToString("") { "%02x".format(it.toInt() and 0xFF) }
        val images = listOf("main", "lite").associateWith { assets.open("firmware/wsn-$it.bin").use { s -> s.readBytes() } }
        val app = applicationContext
        thread {
            try {
                val n = h.knownBoards().size + 1
                val r = OtgProvision({ openPort(app) }, images) { flashText = it }
                    .run(key, OtgProvision.Settings(ssid, password, "", host, HubService.PORT),
                        onProgress = { p -> flashProgress = p; flashText = "Прошивка: $p%" },
                        beforeProvision = { mac -> h.addKey(mac, key, "Плата $n") })  // ключ — в телефон раньше, чем в плату
                h.announce()
                flashResult = r.mac.lowercase()
            } catch (e: OtgProvision.WifiFailed) {
                h.announce()                       // ключ уже в плате и в телефоне; не хватает только сети
                wifiFailMac = e.mac.lowercase(); flashError = e.message
            } catch (e: Exception) {
                flashError = e.message ?: e.javaClass.simpleName
            } finally {
                flashing.set(false)
            }
        }
    }

    /** The board is flashed and keyed but is not on Wi-Fi. The cable is still in: only the network is set again. */
    private fun wifiRetryPage(reason: String?) {
        form.removeAllViews(); buttons.removeAllViews()
        title.text = "Плата не подключилась к Wi-Fi"
        body.text = (reason?.let { "Причина: $it.\n\n" } ?: "") + "Кабель не отключайте. Проверьте имя сети и пароль " +
            "(с учётом заглавных букв) и попробуйте ещё раз — прошивать плату заново не нужно."
        val ssid = EditText(this).apply { hint = "Имя сети Wi-Fi"; setText(currentSsid() ?: ""); setTextColor(Color.WHITE); setHintTextColor(Color.GRAY) }
        val pass = EditText(this).apply {
            hint = "Пароль Wi-Fi"; inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            setTextColor(Color.WHITE); setHintTextColor(Color.GRAY)
        }
        form.addView(ssid); form.addView(pass)
        button("Проверить подключение") {
            val s = ssid.text.toString().trim(); val pw = pass.text.toString()
            if (s.isEmpty()) { body.text = "Введите имя сети."; return@button }
            if (pw.isNotEmpty() && (pw.length < 8 || pw.length > 63)) { body.text = "Пароль Wi-Fi — от 8 до 63 символов."; return@button }
            pass.setText("")
            retryWifi(s, pw)
        }
        button("Прошить плату заново", false) { wifiFailMac = null; cablePage() }
    }

    private fun retryWifi(ssid: String, password: String) {
        if (hub() == null) { body.text = "Центр запускается, подождите секунду."; return }
        if (!flashing.compareAndSet(false, true)) { busy = true; return }
        busy = true
        form.removeAllViews(); buttons.removeAllViews()
        flashText = "Плата пробует подключиться к Wi-Fi…"; flashProgress = 0; flashResult = null; flashError = null
        progress.visibility = android.view.View.VISIBLE; progress.progress = 0
        val app = applicationContext
        thread {
            try {
                val r = OtgProvision({ openPort(app) }, emptyMap()) { flashText = it }
                    .retryWifi(OtgProvision.Settings(ssid, password, "", "", HubService.PORT))
                wifiFailMac = null
                flashResult = r.mac.lowercase()
            } catch (e: OtgProvision.WifiFailed) {
                flashError = e.message
            } catch (e: Exception) {
                flashError = e.message ?: e.javaClass.simpleName
            } finally {
                flashing.set(false)
            }
        }
    }

    private fun ui(text: String) = main.post { body.text = text }
}
