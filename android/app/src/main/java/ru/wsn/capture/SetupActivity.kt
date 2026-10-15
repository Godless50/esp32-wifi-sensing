package ru.wsn.capture

import android.Manifest
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.net.wifi.WifiManager
import android.os.BatteryManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import ru.wsn.capture.esp.EspLoader
import ru.wsn.capture.esp.OtgProvision
import ru.wsn.capture.esp.SerialPort
import ru.wsn.capture.esp.UsbSerialPort
import ru.wsn.capture.hub.HubService
import ru.wsn.capture.ui.Spy
import ru.wsn.capture.ui.Spy.add
import java.security.SecureRandom
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/** Setup without a computer, for people who never flashed anything: the phone becomes the hub.
 *  1) the board by cable: the phone flashes it, gives it the Wi-Fi (the password goes over the cable into the
 *     board only) and waits until the board is on the network — a wrong password is caught with the cable still in;
 *  2) put the board where it will stay — it comes back over Wi-Fi; more boards or go on;
 *  3) quiet minute: each board finds the home devices it hears (nobody moving);
 *  4) the walk with the phone (AR rooms + scenario, with two «everybody out» steps); 5) training on the phone;
 *  then what the model can do, honestly (one zone unless the rooms are told apart), and the hub screen. */
class SetupActivity : AppCompatActivity() {
    private enum class Page { INTRO, BRIEF, CABLE, WIFI, PLACE, MORE, SURVEY, WALK, TRAIN, DONE }

    private val main = Handler(Looper.getMainLooper())
    private var page = Page.INTRO
    private var busy = false
    private var lastBoard: String? = null
    private var placeSince = 0L
    private var surveyStarted = 0L
    private var surveyDone = false
    private var cableError: String? = null
    private var checklistSig = ""
    private val prefs by lazy { getSharedPreferences("wsn-setup", MODE_PRIVATE) }

    // live parts of the current page, updated by refresh() without rebuilding it
    private var liveText: TextView? = null
    private var livePct: TextView? = null
    private var liveBar: ((Int) -> Unit)? = null
    private var liveBox: LinearLayout? = null
    private var liveSig = ""

    companion object {
        const val EXTRA_PAGE = "page"
        private const val USB_PERMISSION = "ru.wsn.capture.USB_PERMISSION"
        private const val STEPS = 5
        /** A flash is running (it outlives a recreated activity: the board re-enumerates and the phone may offer to
         *  open the app again). A second one must not start on the same USB device. */
        private val flashing = java.util.concurrent.atomic.AtomicBoolean(false)
        @Volatile private var flashText = ""
        @Volatile private var flashProgress = 0
        @Volatile private var flashResult: String? = null   // MAC of the board when the last flash finished
        @Volatile private var flashError: String? = null
        /** The board flashed and keyed, but it did not join Wi-Fi: it is still on the cable, ask the password again. */
        @Volatile private var wifiFailMac: String? = null
        @Volatile private var wifiFailReason: String? = null
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        HubService.start(this)
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 8)  // значок работы штаба
        val asked = intent.getStringExtra(EXTRA_PAGE)
        page = try { Page.valueOf(asked ?: prefs.getString("page", "INTRO")!!) } catch (e: Exception) { Page.INTRO }
        if (asked == null && page == Page.TRAIN && hub()?.train?.running != true && hub()?.train?.ok == null) page = Page.WALK
        if (page == Page.CABLE && flashing.get()) busy = true   // прошивка идёт в другом экземпляре экрана
        show()
        main.post(object : Runnable { override fun run() { refresh(); main.postDelayed(this, 1000) } })
    }

    override fun onResume() { super.onResume(); if (page == Page.WALK || page == Page.TRAIN || page == Page.DONE) show() }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        intent.getStringExtra(EXTRA_PAGE)?.let { p -> runCatching { Page.valueOf(p) }.getOrNull()?.let { if (!busy) go(it) } }
        // без страницы: плата переподключилась по USB — экран тот же
    }

    override fun onDestroy() { main.removeCallbacksAndMessages(null); super.onDestroy() }

    private fun go(p: Page) {
        if (p == Page.SURVEY && page != Page.SURVEY) { surveyStarted = 0; surveyDone = false }   // каждый заход — новая минута
        if (p == Page.PLACE && page != Page.PLACE) placeSince = 0
        page = p
        if (p != Page.WIFI) prefs.edit().putString("page", p.name).apply()
        show()
    }

    /** «Закрыть» на шагах: экран закрывается, страница запомнена — приложение откроется там же. */
    private fun closeSetup() { finish() }

    private fun hub() = HubService.current

    private fun step(n: Int, name: String) = "ШАГ $n ИЗ $STEPS · $name"


    // ======================= pages =======================
    private fun show() {
        liveText = null; livePct = null; liveBar = null; liveBox = null; liveSig = ""
        when (page) {
            Page.INTRO -> intro()
            Page.BRIEF -> brief()
            Page.CABLE -> when {
                busy -> flashingPage()
                wifiFailMac != null -> wifiRetryPage()
                else -> cablePage()
            }
            Page.WIFI -> if (busy) flashingPage() else wifiRetryPage(newNetwork = true)
            Page.PLACE -> placePage()
            Page.MORE -> morePage()
            Page.SURVEY -> surveyPage()
            Page.WALK -> walkPage()
            Page.TRAIN -> trainPage()
            Page.DONE -> donePage()
        }
    }

    private fun intro() {
        val c = this; val col = Spy.page(this)
        col.add(LinearLayout(c).apply { gravity = Gravity.CENTER; addView(Spy.sized(c, Spy.MascotView(c, Spy.Mood.LISTEN), 104)) })
        col.add(Spy.h1(c, "Привет, агент!", 32f).apply { gravity = Gravity.CENTER })
        col.add(Spy.text(c, "«Жучок» подскажет, есть ли кто-то дома и двигается ли он. Иногда — даже в какой комнате. " +
            "Он не снимает и не слушает: замечает, как люди меняют Wi-Fi-сигнал.", 17f).apply { gravity = Gravity.CENTER })
        val (note, n) = Spy.paper(c, "ВАЖНО ЗНАТЬ ЗАРАНЕЕ", 0.6f)
        n.add(Spy.text(c, "Этот телефон станет штабом и будет всегда лежать дома на зарядке. Лучше всего — запасной или старый.", 16f, Spy.PAPER_INK), 8)
        n.add(Spy.text(c, "Смотреть, что происходит, можно только на нём. Удалённого доступа нет — зато ничего не уходит в интернет.", 14f, Spy.PAPER_SUB), 8)
        col.add(note, 18)
        col.add(Spy.spring(c))
        col.add(Spy.label(c, "ЭТОТ ТЕЛЕФОН ОСТАНЕТСЯ ДОМА?").apply { gravity = Gravity.CENTER }, 18)
        col.add(Spy.button(c, "Да, это запасной телефон") { go(Page.BRIEF) }, 12)
        col.add(Spy.button(c, "Это мой основной — всё равно продолжить", Spy.Kind.SECONDARY) { go(Page.BRIEF) }, 10)
        if (hub()?.knownBoards()?.isNotEmpty() == true) col.add(Spy.textLink(c, "Жучки уже подключены — дальше") { go(Page.MORE) }, 6)
        col.add(Spy.textLink(c, "У меня есть компьютер с сервером") {
            startActivity(Intent(this, HomeActivity::class.java).putExtra(HomeActivity.EXTRA_CLASSIC, true))
        }, 2)
    }

    private fun brief() {
        val c = this; val col = Spy.page(this)
        col.add(Spy.header(c, "ОПЕРАЦИЯ «ТИХИЙ ДОМ»", Spy.Left.BACK, { go(Page.INTRO) }))
        val (note, n) = Spy.paper(c, "ДОСЬЕ № 001", 0f)
        n.add(Spy.h1(c, "Что понадобится", 22f).apply { setTextColor(Spy.PAPER_INK) }, 4)
        for (s in listOf("Телефон-штаб — останется дома на зарядке", "Жучок и кабель USB-C из набора",
                "Второй блок питания USB — для жучка", "Пароль от домашнего Wi-Fi (сеть 2,4 ГГц)",
                "Камера — один раз при обходе, без записи видео", "Все, кто живёт дома, знают о жучке")) {
            n.add(CheckBox(c).apply {
                text = s; setTextColor(Spy.PAPER_INK); textSize = 15f; typeface = Spy.body(c); minHeight = Spy.dpi(c, 44)
                buttonTintList = ColorStateList.valueOf(Spy.PAPER_INK)
            }, 0)
        }
        col.add(note, 16)
        col.add(Spy.label(c, "ПЛАН · ОКОЛО 40 МИНУТ"), 18)
        val out = Scenario.EMPTY_S / 60
        for ((i, p) in listOf("Подключить жучок кабелем" to "3 мин", "Поставить на место" to "2 мин",
                "Минута тишины" to "1 мин", "Обход квартиры (дважды все выходят на $out мин)" to "20 мин",
                "Обучение" to "до 10 мин").withIndex()) {
            col.add(Spy.hrow(c, Spy.text(c, "${i + 1}", 15f, Spy.MUTED, Spy.head(c)).apply { minWidth = Spy.dpi(c, 18) },
                Spy.text(c, p.first, 15f, Spy.TXT).apply { layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f) },
                Spy.text(c, p.second, 13f, Spy.MUTED, Spy.mono(c))), 6)
        }
        col.add(Spy.spring(c))
        col.add(Spy.button(c, "Начать: подключить жучок") { go(Page.CABLE) }, 20)
    }

    // ---------------- 1. the board by cable ----------------
    private fun cablePage() {
        val c = this; val col = Spy.page(this)
        col.add(Spy.header(c, step(1, "ПОДКЛЮЧЕНИЕ"), Spy.Left.CLOSE, { closeSetup() }))
        cableError?.let { col.add(Spy.banner(c, false, it)) }
        col.add(Spy.h1(c, "Научим жучок вашему Wi-Fi"))
        col.add(Spy.text(c, "Подключите жучок к телефону кабелем USB-C из набора."))
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED)
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.ACCESS_FINE_LOCATION), 7)  // имя сети Wi-Fi
        val (sBox, ssid) = Spy.field(c, "Домашняя сеть", "Имя сети Wi-Fi")
        ssid.setText(currentSsid() ?: "")
        col.add(sBox)
        col.add(Spy.muted(c, "Жучок понимает только сеть 2,4 ГГц. Если у роутера две сети — выберите ту, где нет «5G»."), 6)
        val (pBox, pass) = Spy.field(c, "Пароль Wi-Fi", "Введите пароль", password = true)
        col.add(pBox)
        col.add(Spy.muted(c, "С учётом заглавных букв. Если пароль не подойдёт — узнаем сразу, кабель отключать не придётся."), 6)
        val (note, n) = Spy.paper(c, "ПАРОЛЬ — ТОЛЬКО ЖУЧКУ")
        n.add(Spy.text(c, "Пароль уходит по кабелю только в жучок. Мы не отправляем его в интернет и не храним в телефоне.", 14f, Spy.PAPER_INK), 6)
        col.add(note, 18)
        col.add(Spy.spring(c))
        col.add(Spy.button(c, "Записать программу в жучок") { submit(ssid, pass) { s, pw -> flash(s, pw) } }, 20)
    }

    private fun submit(ssid: EditText, pass: EditText, then: (String, String) -> Unit) {
        val s = ssid.text.toString().trim(); val pw = pass.text.toString()
        if (s.isEmpty()) { ssid.error = "Введите имя сети"; return }
        if (pw.isNotEmpty() && (pw.length < 8 || pw.length > 63)) { pass.error = "Пароль Wi-Fi — от 8 до 63 символов"; return }
        pass.setText("")  // пароль не держим в поле дольше нужного
        then(s, pw)
    }

    private fun flashingPage() {
        val c = this; val col = Spy.page(this)
        col.add(Spy.header(c, step(1, "ПОДКЛЮЧЕНИЕ"), Spy.Left.CLOSE_DISABLED))
        col.add(Spy.h1(c, if (wifiFailMac != null || page == Page.WIFI) "Проверяем пароль" else "Загружаем программу в жучок"))
        val (frame, g) = Spy.gadget(c, "ПРОГРЕСС")
        val pct = Spy.text(c, "0%", 40f, Spy.OK, Spy.head(c)).also { livePct = it }
        val sub = Spy.text(c, "", 15f, Spy.TXT2).also { liveText = it }
        g.addView(Spy.hrow(c, pct, sub))
        val (bar, set) = Spy.progress(c); liveBar = set
        g.add(bar, 10)
        liveBox = Spy.column(c).also { g.add(it, 12) }
        col.add(frame, 18)
        col.add(Spy.row(c, Spy.IconView(c, Spy.Icon.PLUG, Spy.TXT2), "Не отключайте кабель", "Скажем, когда можно — после проверки Wi-Fi"))
        col.add(Spy.spring(c))
        col.add(Spy.button(c, "Подождите…", Spy.Kind.DISABLED), 20)
        refresh()
    }

    private fun wifiRetryPage(newNetwork: Boolean = false) {
        val c = this; val col = Spy.page(this)
        if (newNetwork) {
            col.add(Spy.header(c, "НОВЫЙ ПАРОЛЬ WI-FI", Spy.Left.CLOSE, { finish() }))
            wifiFailReason?.let { col.add(Spy.banner(c, false, it.replaceFirstChar { ch -> ch.uppercase() })) }
            col.add(Spy.h1(c, "Новая сеть для жучка", 24f))
            col.add(Spy.text(c, "Подключите жучок к телефону кабелем. Программу загружать не нужно: жучок получит только имя сети и пароль. " +
                "Минута тишины и обход остаются прежними."))
        } else {
            col.add(Spy.header(c, step(1, "ПОДКЛЮЧЕНИЕ"), Spy.Left.CLOSE, { closeSetup() }))
            col.add(Spy.banner(c, false, wifiFailReason?.replaceFirstChar { it.uppercase() } ?: "Жучок не подключился к Wi-Fi"))
            col.add(Spy.hrow(c, Spy.sized(c, Spy.MascotView(c, Spy.Mood.OFF), 64), Spy.h1(c, "Попробуем ещё раз", 24f)))
            col.add(Spy.text(c, "Кабель не отключайте. Программа уже в жучке — загружать заново не нужно, только сеть и пароль."))
        }
        val (sBox, ssid) = Spy.field(c, "Домашняя сеть", "Имя сети Wi-Fi")
        ssid.setText(currentSsid() ?: "")
        col.add(sBox)
        val (pBox, pass) = Spy.field(c, "Пароль Wi-Fi", "Введите пароль", password = true, error = !newNetwork || wifiFailReason != null)
        col.add(pBox)
        col.add(Spy.muted(c, "Частая причина — заглавные буквы или русская раскладка."), 6)
        col.add(Spy.row(c, Spy.IconView(c, Spy.Icon.INFO, Spy.MUTED), "Пароль точно верный?",
            "Сеть только 5 ГГц жучок не видит. Гостевая сеть и вход через страницу в браузере не подойдут. Жучок далеко от роутера — поднесите ближе."))
        col.add(Spy.spring(c))
        col.add(Spy.button(c, "Проверить подключение") { submit(ssid, pass) { s, pw -> retryWifi(s, pw) } }, 20)
        if (!newNetwork) col.add(Spy.textLink(c, "Загрузить программу заново") { wifiFailMac = null; cableError = null; show() }, 4)
    }

    // ---------------- 2. place ----------------
    private fun placePage() {
        val c = this; val col = Spy.page(this)
        if (placeSince == 0L) placeSince = System.currentTimeMillis()
        val moving = lastBoard == null   // «переставить жучок» из итога: прошивки не было, ждать нечего
        col.add(Spy.header(c, step(2, "УСТАНОВКА"), Spy.Left.CLOSE, { closeSetup() }))
        if (!moving) col.add(Spy.banner(c, true, "Жучок подключился к Wi-Fi. Кабель можно отключить"))
        col.add(Spy.h1(c, if (moving) "Новое место для жучка" else "Поставьте жучок на постоянное место", 24f))
        col.add(Spy.text(c, if (moving) "Ближе к центру квартиры и повыше. После переноса нужны заново минута тишины и обход — старая запись к новому месту не подходит."
            else "Включите его в розетку через свой блок питания — не забирайте зарядку у телефона-штаба. Потом не переставляйте: иначе обход придётся пройти заново."))
        col.add(Spy.row(c, Spy.IconView(c, Spy.Icon.CHECK, Spy.OK), "Открытая полка, повыше"))
        col.add(Spy.row(c, Spy.IconView(c, Spy.Icon.CLOSE, Spy.ALERT), "Не в закрытом шкафу и не за телевизором"), 8)
        col.add(Spy.row(c, Spy.IconView(c, Spy.Icon.CLOSE, Spy.ALERT), "Не вплотную к роутеру"), 8)
        liveBox = Spy.column(c).also { col.add(it, 16) }
        col.add(Spy.spring(c))
        if (moving) col.add(Spy.button(c, "Жучок на новом месте — дальше") { go(Page.MORE) }, 20)
        else col.add(Spy.button(c, "Ждём жучок…", Spy.Kind.DISABLED), 20)
        refresh()
    }

    private fun morePage() {
        val c = this; val col = Spy.page(this)
        col.add(Spy.header(c, step(2, "УСТАНОВКА"), Spy.Left.CLOSE, { closeSetup() }))
        col.add(Spy.h1(c, "Ваши жучки"))
        liveBox = Spy.column(c).also { col.add(it) }
        col.add(Spy.spring(c))
        col.add(Spy.button(c, "Добавить ещё жучок", Spy.Kind.SECONDARY) { cableError = null; go(Page.CABLE) }, 20)
        col.add(Spy.button(c, "Дальше: минута тишины") { go(Page.SURVEY) }, 10)
        refresh()
    }

    // ---------------- 3. quiet minute ----------------
    private fun surveyPage() {
        val c = this; val col = Spy.page(this)
        col.add(Spy.header(c, step(3, "МИНУТА ТИШИНЫ"), Spy.Left.CLOSE, { closeSetup() }))
        col.add(Spy.h1(c, "Минуту никто не ходит"))
        col.add(Spy.text(c, "Пока все стоят, жучок окликает устройства вашей сети — как это делает роутер — и выбирает те, " +
            "чей сигнал поможет замечать людей. Содержимое он не читает."))
        val (frame, g) = Spy.gadget(c, "ТИШИНА")
        val m = Spy.sized(c, Spy.MascotView(c, Spy.Mood.LISTEN), 56)
        val t = Spy.text(c, if (surveyStarted > 0) "Идёт…" else "Попросите домашних тоже не ходить", 15f, Spy.TXT2).also { liveText = it }
        g.addView(Spy.hrow(c, m, t.apply { layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f) }))
        liveBox = Spy.column(c).also { g.add(it, 10) }
        col.add(frame, 18)
        col.add(Spy.muted(c, "Телефоны жучок не берёт сам: их носят по дому."))
        col.add(Spy.spring(c))
        when {
            surveyDone -> col.add(Spy.button(c, "Дальше: обход") { go(Page.WALK) }, 20)
            surveyStarted > 0 -> col.add(Spy.button(c, "Ждём тишину…", Spy.Kind.DISABLED), 20)
            else -> {
                col.add(Spy.button(c, "Начать минуту тишины") { startSurvey() }, 20)
                col.add(Spy.textLink(c, "Пропустить") { go(Page.WALK) }, 4)
            }
        }
        refresh()
    }

    private fun startSurvey() {
        val h = hub() ?: return
        val live = h.boards().map { it.mac }.toSet()
        val sent = h.knownBoards().filter { it in live }.count { h.survey(it) }
        if (sent == 0) { liveText?.text = "Ни один жучок сейчас не на связи. Проверьте их питание."; return }
        surveyStarted = System.currentTimeMillis(); surveyDone = false
        show()
    }

    // ---------------- 4. the walk ----------------
    private fun walkPage() {
        val c = this; val col = Spy.page(this)
        val out = Scenario.EMPTY_S / 60
        col.add(Spy.header(c, step(4, "ОБХОД"), Spy.Left.CLOSE, { closeSetup() }))
        col.add(Spy.h1(c, "Обход квартиры"))
        col.add(Spy.text(c, "Телефон покажет, что делать: сначала камерой отметить жучки и углы комнат, потом пройти по комнатам по подсказкам — около 20 минут."))
        col.add(Spy.row(c, Spy.IconView(c, Spy.Icon.LOCK, Spy.TXT2), "Камера — только чтобы понять, где вы стоите", "Видео не записывается"))
        col.add(Spy.row(c, Spy.IconView(c, Spy.Icon.HOME, Spy.TXT2), "В начале и в конце все выходят из дома на $out мин",
            "Телефон остаётся дома и записывает пустую квартиру. Не трогайте его до возвращения"), 8)
        col.add(Spy.row(c, Spy.IconView(c, Spy.Icon.PAW, Spy.MUTED), "Есть питомец?", "Возьмите с собой или закройте в переноске — жучок примет его за человека"), 8)
        val last = hub()?.sessions()?.firstOrNull()
        col.add(Spy.spring(c))
        col.add(Spy.button(c, "Начать обход") { startActivity(Intent(this, CaptureActivity::class.java)) }, 20)
        if (last != null) col.add(Spy.button(c, "Обход записан — обучить", Spy.Kind.SECONDARY) { hub()?.trainNewest(); go(Page.TRAIN) }, 10)
    }

    // ---------------- 5. training ----------------
    private fun trainPage() {
        val c = this; val col = Spy.page(this)
        val t = hub()?.train
        col.add(Spy.header(c, step(5, "ОБУЧЕНИЕ"), Spy.Left.CLOSE, { finish() }))
        if (t != null && !t.running && t.ok == false) {
            col.add(Spy.banner(c, false, t.message.ifEmpty { "Обучение не получилось" }))
            col.add(Spy.h1(c, "Нужен ещё один обход"))
            col.add(Spy.text(c, "Записи не хватило, чтобы штаб уверенно различал «кто-то дома» и «никого». Пройдите обход ещё раз — это около 20 минут."))
            col.add(Spy.spring(c))
            col.add(Spy.button(c, "Пройти обход заново") { go(Page.WALK) }, 20)
            return
        }
        col.add(Spy.h1(c, "Штаб изучает ваш дом"))
        val (frame, g) = Spy.gadget(c, "ОБУЧЕНИЕ НА ТЕЛЕФОНЕ")
        val pct = Spy.text(c, "0%", 40f, Spy.OK, Spy.head(c)).also { livePct = it }
        g.addView(Spy.hrow(c, pct, Spy.text(c, "", 15f, Spy.TXT2).also { liveText = it }))
        val (bar, set) = Spy.progress(c); liveBar = set
        g.add(bar, 10)
        g.add(Spy.muted(c, "Всё считается здесь, в телефоне. Можно выйти из приложения — обучение продолжится."), 10)
        col.add(frame, 18)
        col.add(Spy.label(c, "ПОКА ЖДЁМ: ШТАБ НА ПОСТУ"), 18)
        col.add(Spy.text(c, "Телефон-штаб работает круглые сутки. Чтобы Android его не усыпил:", 14f), 8)
        checklistSig = ""
        liveBox = Spy.column(c).also { col.add(it, 10) }
        col.add(Spy.spring(c))
        col.add(Spy.button(c, "Обучение идёт…", Spy.Kind.DISABLED), 20)
        refresh()
    }

    /** The «штаб на посту» checklist: notifications, battery optimisation, charger. Pressable while not done. */
    private fun postChecklist(box: LinearLayout, onlyMissing: Boolean = false) {
        val c = this
        val notif = Build.VERSION.SDK_INT < 33 || ContextCompat.checkSelfPermission(c, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
        val pm = getSystemService(Context.POWER_SERVICE) as android.os.PowerManager
        val awake = pm.isIgnoringBatteryOptimizations(packageName)
        val bm = getSystemService(Context.BATTERY_SERVICE) as BatteryManager
        val charging = bm.isCharging
        val level = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY).takeIf { it in 0..100 }  // не все телефоны отдают
        val sig = "$notif$awake$charging${level?.div(5)}"
        if (sig == checklistSig) return     // перестраиваем только при перемене: иначе теряются нажатия и фокус диктора
        checklistSig = sig
        box.removeAllViews()
        if (onlyMissing && notif && awake && charging) return
        if (!(onlyMissing && notif)) box.add(if (notif) Spy.row(c, Spy.IconView(c, Spy.Icon.CHECK, Spy.OK), "Уведомления разрешены")
            else Spy.row(c, Spy.IconView(c, Spy.Icon.BELL, Spy.TXT), "Разрешите уведомления", "Без них Android может остановить штаб",
                onClick = { if (Build.VERSION.SDK_INT >= 33) ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 8) }), 0)
        if (!(onlyMissing && awake)) box.add(if (awake) Spy.row(c, Spy.IconView(c, Spy.Icon.CHECK, Spy.OK), "Экономия батареи не трогает штаб")
            else Spy.row(c, Spy.IconView(c, Spy.Icon.BATT, Spy.TXT), "Не усыплять штаб", "Иначе без зарядки приём может останавливаться",
                onClick = {
                    startActivity(Intent(android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, android.net.Uri.parse("package:$packageName")))
                }), 8)
        if (!(onlyMissing && charging)) box.add(if (charging) Spy.row(c, Spy.IconView(c, Spy.Icon.CHECK, Spy.OK), "Зарядка подключена", right = level?.let { "$it%" })
            else Spy.row(c, Spy.IconView(c, Spy.Icon.PLUG, Spy.TXT), "Подключите зарядку", "Штаб работает от сети круглосуточно", level?.let { "$it%" }), 8)
    }

    // ---------------- result ----------------
    private fun donePage() {
        val c = this; val col = Spy.page(this)
        val h = hub()
        val roomsOk = h?.modelMeta()?.optBoolean("rooms_ok") == true
        col.add(Spy.header(c, "ГОТОВО · ЧТО УМЕЕТ ЖУЧОК"))
        col.add(Spy.hrow(c, Spy.sized(c, Spy.MascotView(c, Spy.Mood.LISTEN), 72), Spy.h1(c, "Жучок на посту")))
        val (frame, g) = Spy.gadget(c, "ИТОГ ПРОВЕРКИ")
        fun line(ok: Boolean, t: String, sub: String) = Spy.hrow(c, Spy.sized(c, Spy.IconView(c, if (ok) Spy.Icon.CHECK else Spy.Icon.CLOSE, if (ok) Spy.OK else Spy.MUTED), 22),
            Spy.column(c).apply { addView(Spy.text(c, t, 16f, Spy.TXT, Spy.bodyBold(c), 1.2f)); addView(Spy.muted(c, sub)) }
                .apply { layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f) }).apply { gravity = Gravity.TOP }
        // проверено на кусках записи, которых модель не видела; без метрики — не обещаем
        val held = h?.modelMeta()?.optJSONObject("metrics")?.optJSONObject("heldout")
        fun verdict(key: String, what: String, onWhat: String): android.view.View {
            val acc = held?.optDouble(key, Double.NaN) ?: Double.NaN
            return when {
                acc.isNaN() -> line(false, what, "Не проверено: в записи не хватило данных. Пройдите обход ещё раз")
                acc >= 0.8 -> line(true, what, "Проверено на $onWhat: верно в ${(acc * 100).toInt()} % случаев")
                else -> line(false, "$what — пока ненадёжно", "Верно только в ${(acc * 100).toInt()} % случаев. Помогут второй жучок или новый обход")
            }
        }
        g.addView(verdict("nobody_home_bal_acc", "Кто-то дома или никого", "записи, когда все выходили"))
        g.add(verdict("moving_bal_acc", "Есть ли движение", "обходе"))
        g.add(if (roomsOk) line(true, "Различает комнаты", "Покажем, в какой комнате кто-то есть")
              else line(false, "Комнаты пока не различает", "Будем показывать весь дом одной зоной — так честнее"))
        h?.train?.message?.takeIf { it.isNotBlank() }?.let { g.add(Spy.muted(c, it), 12) }
        col.add(frame, 18)
        if (!roomsOk) {
            col.add(Spy.label(c, "ХОТИТЕ ВИДЕТЬ КОМНАТЫ?"), 18)
            col.add(Spy.row(c, Spy.IconView(c, Spy.Icon.PLUS, Spy.TXT2), "Добавить второй жучок",
                "Нужен ещё один жучок. Потом заново: тишина и обход", "≈40 мин", onClick = { cableError = null; go(Page.CABLE) }), 10)
            col.add(Spy.row(c, Spy.IconView(c, Spy.Icon.HOME, Spy.TXT2), "Переставить жучок",
                "Ближе к центру, повыше. Потом заново минута тишины и обход", "≈40 мин", onClick = { lastBoard = null; go(Page.PLACE) }), 8)
        }
        checklistSig = ""
        liveBox = Spy.column(c).also { col.add(it, 12); postChecklist(it, onlyMissing = true) }
        col.add(Spy.muted(c, "Питомцы тоже считаются движением."), 12)
        col.add(Spy.spring(c))
        col.add(Spy.button(c, "Открыть штаб") { startActivity(Intent(this, HubMapActivity::class.java)); finish() }, 20)
    }

    // ======================= live updates =======================
    private fun refresh() {
        val h = hub()
        when (page) {
            Page.CABLE, Page.WIFI -> {
                flashError?.let { err ->
                    flashError = null; busy = false
                    if (page == Page.WIFI || wifiFailMac != null) wifiFailReason = err else cableError = err
                    show(); return
                }
                flashResult?.let {
                    flashResult = null; busy = false; wifiFailMac = null; wifiFailReason = null
                    if (page == Page.WIFI) { finish(); return }   // известный жучок с новым паролем: обратно в штаб
                    lastBoard = it; go(Page.PLACE); return
                }
                if (!busy) return
                val stage = when { flashText.contains("Wi-Fi") -> 2; flashProgress > 0 -> 1; else -> 0 }
                livePct?.text = if (stage == 2) "Wi-Fi" else "$flashProgress%"
                liveText?.text = when (stage) { 0 -> "связь с жучком…"; 1 -> "около минуты"; else -> "ждём, пустит ли роутер" }
                liveBar?.invoke(if (stage == 2) 100 else flashProgress)
                val sig = "c$stage"
                if (sig != liveSig) {
                    liveSig = sig
                    liveBox?.let { b ->
                        b.removeAllViews()
                        b.addView(Spy.stage(this, if (stage > 0) 2 else 1, "Связь с жучком"))
                        b.addView(Spy.stage(this, when { stage > 1 -> 2; stage == 1 -> 1; else -> 0 }, "Загрузка программы", if (stage == 1) "$flashProgress%" else ""))
                        b.addView(Spy.stage(this, if (stage == 2) 1 else 0, "Проверка Wi-Fi: пустит ли роутер"))
                    }
                }
            }
            Page.PLACE -> {
                val mac = lastBoard ?: return   // перенос жучка: ждать нечего, кнопка «дальше» уже есть
                if (h != null && h.boards().any { it.mac == mac }) { main.postDelayed({ if (page == Page.PLACE) go(Page.MORE) }, 800); return }
                val late = System.currentTimeMillis() - placeSince > 120_000
                val sig = "p$late"
                if (sig == liveSig) return
                liveSig = sig
                liveBox?.let { b ->
                    b.removeAllViews()
                    if (!late) b.addView(Spy.row(this, Spy.sized(this, Spy.MascotView(this, Spy.Mood.OFF), 36), "Ждём жучок в сети…",
                        "Обычно до минуты после включения"))
                    else {
                        b.addView(Spy.banner(this, false, "Жучок не появился в сети за 2 минуты"))
                        b.add(Spy.row(this, Spy.IconView(this, Spy.Icon.WIFI, Spy.TXT2), "Далеко от роутера?", "Поставьте ближе — на постоянном месте сигнал может не доставать"), 8)
                        b.add(Spy.row(this, Spy.IconView(this, Spy.Icon.LOCK, Spy.TXT2), "Телефон в той же сети?", "Гостевая сеть часто не пускает устройства друг к другу"), 8)
                        b.add(Spy.row(this, Spy.IconView(this, Spy.Icon.PLUG, Spy.TXT2), "Выключите и включите жучок", "Выньте блок питания из розетки на 5 секунд"), 8)
                    }
                }
            }
            Page.MORE -> {
                if (h == null) return
                val known = h.knownBoards(); val live = h.boards().map { it.mac }.toSet()
                val sig = known.joinToString { it + (it in live) }
                if (sig == liveSig) return
                liveSig = sig
                liveBox?.let { b ->
                    b.removeAllViews()
                    if (known.isEmpty()) b.addView(Spy.text(this, "Жучков пока нет."))
                    for (mac in known) b.add(Spy.row(this, Spy.sized(this, Spy.MascotView(this, if (mac in live) Spy.Mood.LISTEN else Spy.Mood.OFF), 36),
                        h.boardName(mac), if (mac in live) "на связи" else "нет связи"), 8)
                    if (known.size == 1) b.add(Spy.muted(this, "С одним жучком будет видно «кто-то дома / никого» и движение. " +
                        "Комнаты — если проверка покажет, что жучок их различает; второй жучок помогает."), 12)
                }
            }
            Page.SURVEY -> if (h != null && surveyStarted > 0 && !surveyDone) {
                val boards = h.knownBoards()
                val lines = boards.map { mac ->
                    val s = h.surveys[mac]; val name = h.boardName(mac)
                    when {
                        s == null && System.currentTimeMillis() - surveyStarted > 150_000 -> "$name: не ответил — пропустим"
                        s == null -> "$name: ждём ответа…"
                        s!!.state == 3 -> "$name: устройств в сети ${s.responders}, помощников ${s.entries.count { it.flags and 1 != 0 }}"
                        s.state == 4 -> "$name: не получилось — пропустим"
                        else -> "$name: ${s.progress}%"
                    }
                }
                liveBox?.let { b -> b.removeAllViews(); for (l in lines) b.addView(Spy.text(this, l, 14f, Spy.TXT2, Spy.mono(this))) }
                val left = 60 - ((System.currentTimeMillis() - surveyStarted) / 1000).toInt()
                liveText?.text = if (left > 0) "Осталось около ${left} с. Попросите домашних не ходить" else "Заканчиваем…"
                val done = boards.all { (h.surveys[it]?.state ?: 0) >= 3 }
                if (done || System.currentTimeMillis() - surveyStarted > 150_000) {
                    surveyDone = true; show()
                    liveBox?.let { b -> for (l in lines) b.addView(Spy.text(this, l, 14f, Spy.TXT2, Spy.mono(this))) }
                    liveText?.text = "Готово. Можно ходить"
                }
            }
            Page.TRAIN -> {
                val t = h?.train ?: HubService.TrainState(true, "штаб запускается", 0.0, "")
                if (!t.running && t.ok == true) { go(Page.DONE); return }
                if (!t.running && t.ok == false) { if (liveSig != "fail") { liveSig = "fail"; show() }; return }
                livePct?.text = "${(t.progress * 100).toInt()}%"
                liveText?.text = t.stage.ifEmpty { "готовимся" }
                liveBar?.invoke((t.progress * 100).toInt())
                liveBox?.let { postChecklist(it) }
            }
            Page.DONE -> liveBox?.let { postChecklist(it, onlyMissing = true) }
            else -> {}
        }
    }

    // ======================= the cable =======================
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
        throw EspLoader.EspError("жучок не найден по кабелю")
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

    private fun startJob(firstText: String): Boolean {
        if (!flashing.compareAndSet(false, true)) { busy = true; show(); return false }
        busy = true; cableError = null
        flashText = firstText; flashProgress = 0; flashResult = null; flashError = null
        show()
        return true
    }

    private fun flash(ssid: String, password: String) {
        val h = hub() ?: run { cableError = "штаб ещё запускается, нажмите через секунду"; show(); return }
        val host = h.localIp() ?: run { cableError = "телефон не подключён к Wi-Fi — подключите его к домашней сети"; show(); return }
        val images = try { listOf("main", "lite").associateWith { assets.open("firmware/wsn-$it.bin").use { s -> s.readBytes() } } }
            catch (e: java.io.IOException) { cableError = "В приложении нет программы для жучка — эта сборка неполная"; show(); return }
        if (!startJob("Связь с жучком…")) return
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
                            h.addKey(mac, key, if (m in h.knownBoards()) h.boardName(m) else "Жучок $n")
                        })  // ключ — в телефон раньше, чем в плату
                h.announce()
                flashResult = r.mac.lowercase()
            } catch (e: OtgProvision.WifiFailed) {
                h.announce()                       // ключ уже в плате и в телефоне; не хватает только сети
                wifiFailMac = e.mac.lowercase(); flashError = e.message
            } catch (e: OtgProvision.NetworkNotSeen) {
                flashError = "Жучок не видит сеть «${e.ssid}». Проверьте имя сети и что она 2,4 ГГц"
            } catch (e: Exception) {
                flashError = "Не получилось: ${e.message ?: e.javaClass.simpleName}. Проверьте кабель — нужен кабель с передачей данных — и попробуйте ещё раз"
            } finally {
                flashing.set(false)
            }
        }
    }

    private fun retryWifi(ssid: String, password: String) {
        if (hub() == null) return
        if (!startJob("Проверяем Wi-Fi…")) return
        val app = applicationContext
        thread {
            try {
                val r = OtgProvision({ openPort(app) }, emptyMap()) { flashText = it }
                    .retryWifi(OtgProvision.Settings(ssid, password, "", "", HubService.PORT))
                wifiFailMac = null
                flashResult = r.mac.lowercase()
            } catch (e: OtgProvision.WifiFailed) {
                flashError = e.message
            } catch (e: OtgProvision.NetworkNotSeen) {
                flashError = "жучок не видит сеть «${e.ssid}» — проверьте имя сети и что она 2,4 ГГц"
            } catch (e: Exception) {
                flashError = "не получилось: ${e.message ?: e.javaClass.simpleName}"
            } finally {
                flashing.set(false)
            }
        }
    }

}
