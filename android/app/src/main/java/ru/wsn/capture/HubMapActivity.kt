package ru.wsn.capture

import android.content.Context
import android.content.Intent
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject
import ru.wsn.capture.hub.HubService
import ru.wsn.capture.ui.Spy
import ru.wsn.capture.ui.Spy.add
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import kotlin.math.max
import kotlin.math.min

/** «Штаб»: the main screen when this phone is the hub. What is home now (somebody / nobody, moving or not), on
 *  the plan of the walk when the model tells rooms apart, else the whole flat as one zone; the day's events; and
 *  an honest «нет связи» when the boards are silent — old data is never shown as current. */
class HubMapActivity : AppCompatActivity() {
    private val main = Handler(Looper.getMainLooper())
    private var sig = ""
    private var fresh: TextView? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        HubService.start(this)
        main.post(object : Runnable { override fun run() { update(); main.postDelayed(this, 1000) } })
    }

    override fun onDestroy() { main.removeCallbacksAndMessages(null); super.onDestroy() }

    private enum class Mode { STARTING, NO_MODEL, OFFLINE, LISTENING, EMPTY, PRESENT }

    private fun update() {
        val h = HubService.current
        val meta = h?.modelMeta()
        val live = h?.boards(h.offlineAfterS) ?: emptyList()
        val known = h?.knownBoards() ?: emptyList()
        val v = h?.verdict
        val roomsOk = meta?.optBoolean("rooms_ok") == true
        val mode = when {
            h == null -> Mode.STARTING
            meta == null || known.isEmpty() -> Mode.NO_MODEL
            live.isEmpty() && h.lastHeard() == null && h.now() - h.startedAt < h.offlineAfterS -> Mode.LISTENING  // штаб только запустился
            live.isEmpty() -> Mode.OFFLINE
            v == null -> Mode.LISTENING
            v.emptyP >= 0.5 -> Mode.EMPTY
            else -> Mode.PRESENT
        }
        val moving = v != null && v.movingP >= 0.5
        val zone = if (mode == Mode.PRESENT && roomsOk) v?.zone else null
        val newSig = listOf(mode, moving, zone, roomsOk, live.size, known.size, h?.events?.events()?.firstOrNull()?.t, (startOfDay() / 86400).toLong(),
            if (mode == Mode.OFFLINE) ((h?.now() ?: 0.0) - (h?.lastHeard() ?: 0.0)).toInt() / 60 else 0).joinToString("|")
        if (newSig != sig) { sig = newSig; render(h, meta, mode, moving, zone, roomsOk, live.size, known.size) }
        // настоящий возраст данных, а не «только что»: вердикт строится по последнему, что слышали жучки
        if (mode == Mode.PRESENT || mode == Mode.EMPTY) h?.lastHeard()?.let { t ->
            val age = (h.now() - t).toInt()
            fresh?.text = if (age < 3) "только что" else "$age с назад"
        }
    }

    private fun render(h: HubService?, meta: JSONObject?, mode: Mode, moving: Boolean, zone: String?, roomsOk: Boolean, live: Int, known: Int) {
        val oldScroll = (window.decorView.findViewById<View>(android.R.id.content) as? android.view.ViewGroup)?.getChildAt(0)?.scrollY ?: 0
        val col = Spy.page(this)
        val c = this
        // ---- header ----
        val titles = Spy.column(c).apply {
            addView(Spy.text(c, "ЖУЧОК", 26f, Spy.TXT, Spy.head(c)).apply { letterSpacing = 0.12f })
            addView(Spy.label(c, "ШТАБ · ВАШ ДОМ"))
        }
        col.add(Spy.hrow(c, titles.apply { layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f) },
            Spy.iconButton(c, Spy.Icon.GEAR, "Настройки") { settings() }))

        if (mode == Mode.STARTING || mode == Mode.NO_MODEL) {
            col.add(Spy.h1(c, if (mode == Mode.STARTING) "Штаб запускается…" else "Жучок ещё не настроен"), 24)
            if (mode == Mode.NO_MODEL) {
                col.add(Spy.text(c, "Пройдите настройку: подключение жучка, обход и обучение — около 40 минут."))
                col.add(Spy.spring(c))
                col.add(Spy.button(c, "Начать настройку") { setup(null) })
            }
            return
        }

        // ---- the gadget: state now ----
        val (frame, g) = Spy.gadget(c, "ЖУЧОК · МОД. Ж-1")
        val mood = when (mode) { Mode.OFFLINE -> Spy.Mood.OFF; Mode.PRESENT -> Spy.Mood.DETECT; else -> Spy.Mood.LISTEN }
        val m = Spy.sized(c, Spy.MascotView(c, mood), 60)
        val st = Spy.column(c)
        st.addView(Spy.label(c, if (mode == Mode.OFFLINE) "НЕТ СВЯЗИ" else "СЕЙЧАС ДОМА", if (mode == Mode.OFFLINE) Spy.ALERT else Spy.MUTED))
        val (title, sub) = when (mode) {
            Mode.OFFLINE -> "Жучок молчит" to lastHeardText(h)
            Mode.LISTENING -> "Слушаю жучок…" to "Штаб только что запустился — до минуты"
            Mode.EMPTY -> "Никого дома" to "Тихо"
            else -> (if (zone != null) "Кто-то: $zone" else "Кто-то дома") to (if (moving) "Есть движение" else "Без движения")
        }
        st.addView(Spy.text(c, title, 26f, Spy.TXT, Spy.head(c), 1.1f))
        st.addView(Spy.text(c, sub, 15f, Spy.TXT2))
        g.addView(Spy.hrow(c, m, st.apply { layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f) }))
        if (mode == Mode.OFFLINE) {
            g.add(Spy.text(c, "Что дома сейчас — не знаем. Старые данные не показываем как текущие.", 14f, Spy.MUTED))
        } else {
            val zv = ZoneView(c)
            zv.meta = meta; zv.roomsOk = roomsOk; zv.active = if (mode == Mode.PRESENT) (zone ?: "") else null
            g.add(zv.apply { layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, Spy.dpi(c, if (roomsOk) 260 else 160)) })
            val chips = Spy.hrow(c, Spy.chip(c, if (known > 1) "жучки: $live из $known на связи" else "жучок на связи"),
                Spy.chip(c, "только что", Spy.MUTED).also { fresh = it }, gapDp = 8).apply { gravity = Gravity.CENTER }
            g.add(chips)
        }
        col.add(frame, 18)

        if (mode == Mode.OFFLINE) {
            col.add(Spy.label(c, "ЧТО ПРОВЕРИТЬ"), 18)
            col.add(Spy.row(c, Spy.IconView(c, Spy.Icon.PLUG, Spy.TXT2), "Жучок включён в розетку?", "Блок питания мог выпасть или выключиться"), 10)
            col.add(Spy.row(c, Spy.IconView(c, Spy.Icon.WIFI, Spy.TXT2), "Wi-Fi дома работает?", "Телефон-штаб должен быть в той же сети, что и жучок"), 8)
            col.add(Spy.row(c, Spy.IconView(c, Spy.Icon.LOCK, Spy.TXT2), "Меняли пароль Wi-Fi?", "Тогда жучку нужен новый — снова по кабелю"), 8)
        }

        // ---- the day's events ----
        val (note, ev) = Spy.paper(c, "СОБЫТИЯ ЗА СЕГОДНЯ", -0.6f)
        val today = startOfDay()
        val list = h?.events?.events()?.filter { it.t >= today }?.take(6) ?: emptyList()
        if (list.isEmpty()) ev.addView(Spy.text(c, "Пока ничего не случилось.", 15f, Spy.PAPER_INK))
        val fmt = SimpleDateFormat("HH:mm", Locale("ru"))
        for (e in list) ev.addView(Spy.eventLine(c, fmt.format(Date((e.t * 1000).toLong())), h!!.events.text(e)))
        col.add(note, 22)

        col.add(Spy.spring(c))
        if (mode == Mode.OFFLINE) col.add(Spy.button(c, "Сменили пароль Wi-Fi — подключить кабелем", Spy.Kind.SECONDARY) { setup("WIFI") }, 18)
        else if (!roomsOk) col.add(Spy.row(c, Spy.IconView(c, Spy.Icon.INFO, Spy.TXT2), "Хотите видеть комнаты?",
            "Сейчас весь дом — одна зона", onClick = { setup("DONE") }), 18)
        val sv = (window.decorView.findViewById<View>(android.R.id.content) as android.view.ViewGroup).getChildAt(0) as? ScrollView
        sv?.post { sv.scrollTo(0, oldScroll) }
    }

    /** For screenshots (ScreensTest): draw a given state without a running hub. */
    internal fun preview(mode: String, moving: Boolean, zone: String?, roomsOk: Boolean, live: Int, known: Int, meta: JSONObject?) {
        main.removeCallbacksAndMessages(null)
        sig = "preview"
        render(null, meta, Mode.valueOf(mode), moving, zone, roomsOk, live, known)
    }

    private fun lastHeardText(h: HubService?): String {
        val t = h?.lastHeard() ?: return "С запуска штаба жучок ни разу не отозвался"
        val min = ((h.now() - t) / 60).toInt()
        return "Последний сигнал в " + SimpleDateFormat("HH:mm", Locale("ru")).format(Date((t * 1000).toLong())) +
            if (min >= 1) " — $min мин назад" else ""
    }

    private fun startOfDay(): Double = Calendar.getInstance().apply {
        set(Calendar.HOUR_OF_DAY, 0); set(Calendar.MINUTE, 0); set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0)
    }.timeInMillis / 1000.0

    private fun setup(page: String?) {
        startActivity(Intent(this, SetupActivity::class.java).apply { if (page != null) putExtra(SetupActivity.EXTRA_PAGE, page) })
    }

    private fun settings() {
        val items = arrayOf("Добавить жучок", "Пройти обход заново", "Сменили пароль Wi-Fi — подключить жучок кабелем", "Режим с компьютером")
        AlertDialog.Builder(this).setTitle("Настройки штаба").setItems(items) { _, i ->
            when (i) {
                0 -> setup("CABLE")
                2 -> setup("WIFI")
                1 -> setup("WALK")
                3 -> startActivity(Intent(this, HomeActivity::class.java).putExtra(HomeActivity.EXTRA_CLASSIC, true))
            }
        }.setNegativeButton("Закрыть", null).show()
    }

    /** The flat: the rooms of the walk when the model tells them apart, else one zone. `active`: null = nobody,
     *  "" = somebody (one zone), a room name = somebody there. Boards are squares at their marked places. */
    class ZoneView(ctx: Context) : View(ctx) {
        var meta: JSONObject? = null
        var roomsOk = false
        var active: String? = null
        private val d = ctx.resources.displayMetrics.density
        private val wall = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 1.5f * d; color = Spy.CTRL }
        private val hot = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 3 * d; color = Spy.PRES; strokeJoin = Paint.Join.ROUND }
        private val hatch = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 3 * d; color = Spy.PRES; alpha = 90 }
        private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Spy.DEEP }
        private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply { textSize = 15 * d; textAlign = Paint.Align.CENTER; typeface = Spy.body(ctx) }
        private val bugFill = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Spy.DEEP }
        private val bugStroke = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 2 * d; color = Spy.OK }
        private val bugDot = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Spy.OK }

        private fun hatchIn(c: Canvas, p: Path) {
            c.save(); c.clipPath(p)
            val step = 10 * d
            var x = -height.toFloat()
            while (x < width) { c.drawLine(x, height.toFloat(), x + height, 0f, hatch); x += step }
            c.restore()
        }

        private fun bug(c: Canvas, x: Float, y: Float) {
            val r = RectF(x - 10 * d, y - 10 * d, x + 10 * d, y + 10 * d)
            c.drawRoundRect(r, 4 * d, 4 * d, bugFill); c.drawRoundRect(r, 4 * d, 4 * d, bugStroke); c.drawCircle(x, y, 4 * d, bugDot)
        }

        override fun onDraw(c: Canvas) {
            try { draw0(c) } catch (e: Exception) { android.util.Log.w("ZoneView", "plan", e); meta = null; roomsOk = false; draw0(c) }  // битый план: одна зона
        }

        private fun draw0(c: Canvas) {
            val m = meta
            val rooms = m?.optJSONArray("rooms")
            if (!roomsOk || rooms == null || rooms.length() == 0) {
                // весь дом одной зоной: точки нет — место внутри неизвестно
                val w = min(width.toFloat(), 300 * d); val x0 = (width - w) / 2; val hgt = height - 8 * d
                val p = Path().apply { moveTo(x0, hgt * 0.36f); lineTo(x0 + w / 2, 4 * d); lineTo(x0 + w, hgt * 0.36f); lineTo(x0 + w, hgt); lineTo(x0, hgt); close() }
                c.drawPath(p, fill)
                if (active != null) { hatchIn(c, p); c.drawPath(p, hot) } else c.drawPath(p, wall)
                text.color = if (active != null) Spy.PRES else Spy.MUTED
                c.drawText(if (active != null) "весь дом" else "весь дом · никого", width / 2f, hgt * 0.68f, text)
                bug(c, x0 + w - 26 * d, hgt - 22 * d)
                return
            }
            val polys = (0 until rooms.length()).map { i ->
                val r = rooms.getJSONObject(i); val pp = r.getJSONArray("polygon")
                r.getString("name") to (0 until pp.length()).map { k -> pp.getJSONArray(k).let { doubleArrayOf(it.getDouble(0), it.getDouble(1)) } }
            }.filter { it.second.size >= 3 }
            val pts = polys.flatMap { it.second }
            if (pts.isEmpty()) return
            val x0 = pts.minOf { it[0] }; val x1 = pts.maxOf { it[0] }; val y0 = pts.minOf { it[1] }; val y1 = pts.maxOf { it[1] }
            val pad = 12 * d
            val s = min((width - 2 * pad) / max(x1 - x0, 0.5), (height - 2 * pad) / max(y1 - y0, 0.5))
            val ox = (width - (x1 - x0) * s) / 2; val oy = (height - (y1 - y0) * s) / 2
            fun X(x: Double) = (ox + (x - x0) * s).toFloat()
            fun Y(y: Double) = (height - oy - (y - y0) * s).toFloat()   // север вверх
            for ((name, poly) in polys) {
                val path = Path().apply { moveTo(X(poly[0][0]), Y(poly[0][1])); for (p in poly.drop(1)) lineTo(X(p[0]), Y(p[1])); close() }
                val here = active != null && active == name
                c.drawPath(path, fill)
                if (here) { hatchIn(c, path); c.drawPath(path, hot) } else c.drawPath(path, wall)
                val cx = poly.sumOf { it[0] } / poly.size; val cy = poly.sumOf { it[1] } / poly.size
                text.color = if (here) Spy.PRES else Spy.TXT2
                c.drawText(name, X(cx), Y(cy), text)
            }
            val boards = m.optJSONObject("boards")
            boards?.keys()?.forEach { mac ->
                val xy = boards.optJSONArray(mac) ?: return@forEach
                bug(c, X(xy.getDouble(0)), Y(xy.getDouble(1)))
            }
        }
    }

}
