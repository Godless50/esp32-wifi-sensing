package ru.wsn.capture

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject
import kotlin.math.max
import kotlin.math.min

/** Live map: rooms with movement (the flat's model, or the radio links before training) and the person. */
class MapActivity : AppCompatActivity() {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var map: PlanView
    private lateinit var status: TextView
    private var transport: Transport? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Server.init(this)
        if (!Server.paired()) { finish(); return }
        status = TextView(this).apply {
            setTextColor(Color.WHITE); textSize = 18f; setPadding(32, 56, 32, 24); setBackgroundColor(Color.rgb(16, 19, 24))
            text = "Загрузка плана…"
        }
        map = PlanView(this)
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.rgb(11, 13, 17))
            addView(status, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
            addView(map, LinearLayout.LayoutParams(MATCH_PARENT, 0, 1f))
        })
        loadPlan()
        transport = Transport("/ws/live?types=person,rooms",
            onOpen = { },
            onMessage = { onLive(it) },
            onClosed = { reason -> status.text = "Нет связи с сервером: $reason" },
        ).also { it.connect() }
        main.postDelayed(object : Runnable { override fun run() { loadPlan(); main.postDelayed(this, 60_000) } }, 60_000)
    }

    override fun onDestroy() {
        super.onDestroy()
        transport?.close()
        main.removeCallbacksAndMessages(null)
    }

    private fun loadPlan() {
        Server.getJson("/api/plan") { js, err ->
            if (js == null) { status.text = "План не загрузился: $err"; return@getJson }
            map.setPlan(js)
            if (map.rooms.isEmpty()) status.text = "Плана ещё нет: «Разметка и запись» в приложении."
        }
    }

    private fun onLive(text: String) {
        val m = try { JSONObject(text) } catch (e: Exception) { return }
        when (m.optString("type")) {
            "rooms" -> {
                map.setRooms(m)
                if (m.optBoolean("nobody_home")) status.text = "Дома никого"
            }
            "person" -> {
                map.setPerson(m)
                if (!map.nobodyHome) {
                    val src = if (map.source == "model") "модель квартиры" else "по радиолиниям (модель ещё не обучена)"
                    status.text = when {
                        !m.optBoolean("present") -> "${m.optString("status")} · $src"
                        else -> "${m.optString("zone")} · ${m.optString("status")} · $src"
                    }
                }
            }
        }
    }
}

/** The plan in metres drawn to fit the view; +y of the plan is up on the screen. */
class PlanView(ctx: Context) : View(ctx) {
    data class Room(val name: String, val poly: List<FloatArray>)

    var rooms: List<Room> = emptyList()
        private set
    private var boards: List<FloatArray> = emptyList()
    private var doors: List<FloatArray> = emptyList()
    private val state = mutableMapOf<String, String>()
    private var person: FloatArray? = null
    private var spread = 0f
    var nobodyHome = false
        private set
    var source = "links"
        private set

    private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val edge = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 3f; color = Color.argb(150, 255, 255, 255) }
    private val label = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.WHITE; textSize = 36f; textAlign = Paint.Align.CENTER }
    private val dot = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.rgb(59, 130, 246) }
    private val halo = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.argb(60, 59, 130, 246) }
    private val boardP = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.rgb(45, 212, 191) }
    private val doorP = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.rgb(200, 200, 200); strokeWidth = 8f }

    fun setPlan(js: JSONObject) {
        val rs = mutableListOf<Room>()
        val add = { arr: org.json.JSONArray? ->
            for (i in 0 until (arr?.length() ?: 0)) {
                val r = arr!!.getJSONObject(i)
                val p = r.getJSONArray("polygon")
                rs += Room(r.getString("name"), (0 until p.length()).map { k ->
                    val xy = p.getJSONArray(k); floatArrayOf(xy.getDouble(0).toFloat(), xy.getDouble(1).toFloat())
                })
            }
        }
        add(js.optJSONArray("rooms"))
        add(js.optJSONArray("zones"))  // зоны внутри комнат рисуются поверх своих комнат
        rooms = rs
        val b = js.optJSONArray("boards")
        boards = (0 until (b?.length() ?: 0)).map { i ->
            val xy = b!!.getJSONObject(i).getJSONArray("xy"); floatArrayOf(xy.getDouble(0).toFloat(), xy.getDouble(1).toFloat())
        }
        val o = js.optJSONArray("objects")
        doors = (0 until (o?.length() ?: 0)).map { o!!.getJSONObject(it) }
            .filter { it.optString("type") in setOf("дверь", "арка") }
            .map { val xy = it.getJSONArray("xy"); floatArrayOf(xy.getDouble(0).toFloat(), xy.getDouble(1).toFloat()) }
        invalidate()
    }

    fun setRooms(m: JSONObject) {
        val arr = m.optJSONArray("rooms") ?: return
        for (i in 0 until arr.length()) arr.getJSONObject(i).let { state[it.getString("name")] = it.optString("state") }
        nobodyHome = m.optBoolean("nobody_home")
        source = m.optString("source", source)
        invalidate()
    }

    fun setPerson(m: JSONObject) {
        val xy = m.optJSONArray("xy")
        person = if (m.optBoolean("present") && xy != null) floatArrayOf(xy.getDouble(0).toFloat(), xy.getDouble(1).toFloat()) else null
        spread = m.optDouble("spread_m", 0.0).toFloat()
        invalidate()
    }

    override fun onDraw(c: Canvas) {
        super.onDraw(c)
        if (rooms.isEmpty()) return
        val pts = rooms.flatMap { it.poly }
        val x0 = pts.minOf { it[0] }; val x1 = pts.maxOf { it[0] }
        val y0 = pts.minOf { it[1] }; val y1 = pts.maxOf { it[1] }
        val pad = 40f
        val s = min((width - 2 * pad) / max(x1 - x0, 0.5f), (height - 2 * pad) / max(y1 - y0, 0.5f))
        val ox = (width - s * (x1 - x0)) / 2; val oy = (height - s * (y1 - y0)) / 2
        val sx = { x: Float -> ox + (x - x0) * s }
        val sy = { y: Float -> height - (oy + (y - y0) * s) }
        for (r in rooms) {
            val path = Path()
            r.poly.forEachIndexed { i, p -> if (i == 0) path.moveTo(sx(p[0]), sy(p[1])) else path.lineTo(sx(p[0]), sy(p[1])) }
            path.close()
            fill.color = when (if (nobodyHome) "тихо" else state[r.name]) {
                "движение" -> Color.argb(190, 245, 158, 11)
                "слабое" -> Color.argb(110, 245, 158, 11)
                else -> Color.argb(120, 55, 65, 81)
            }
            c.drawPath(path, fill)
            c.drawPath(path, edge)
            val cx = r.poly.map { it[0] }.average().toFloat(); val cy = r.poly.map { it[1] }.average().toFloat()
            c.drawText(r.name, sx(cx), sy(cy), label)
        }
        for (d in doors) c.drawCircle(sx(d[0]), sy(d[1]), 7f, doorP)
        for (b in boards) c.drawRect(sx(b[0]) - 10, sy(b[1]) - 10, sx(b[0]) + 10, sy(b[1]) + 10, boardP)
        person?.let { p ->
            if (!nobodyHome) {
                c.drawCircle(sx(p[0]), sy(p[1]), max(18f, spread * s), halo)
                c.drawCircle(sx(p[0]), sy(p[1]), 16f, dot)
            }
        }
    }
}
