package ru.wsn.capture

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject
import ru.wsn.capture.core.ZoneModel
import ru.wsn.capture.hub.HubService
import kotlin.math.max
import kotlin.math.min

/** The map when this phone is the hub: the rooms of the walk (from the trained model), the boards, and where
 *  somebody is now. With rooms the model can not tell apart (one board, or rooms that look alike to the radio)
 *  the flat is one zone and the map says only "somebody / nobody, moving / still". */
class HubMapActivity : AppCompatActivity() {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var status: TextView
    private lateinit var plan: PlanView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        HubService.start(this)
        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(32, 64, 32, 32); setBackgroundColor(Color.rgb(11, 13, 17)) }
        status = TextView(this).apply { textSize = 20f; setTextColor(Color.WHITE); setPadding(0, 0, 0, 24) }
        plan = PlanView(this)
        col.addView(status)
        col.addView(plan, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
        setContentView(col)
        main.post(object : Runnable { override fun run() { update(); main.postDelayed(this, 1000) } })
    }

    private fun update() {
        val h = HubService.current
        if (h == null) { status.text = "Центр запускается…"; return }
        val meta = h.modelMeta()
        plan.meta = meta
        val v = h.verdict
        plan.verdict = v
        status.text = when {
            meta == null -> "Модели ещё нет: пройдите настройку и обход."
            h.boards().isEmpty() -> "Платы не на связи"
            v == null -> "Слушаю платы…"
            else -> verdictText(v, meta.optBoolean("rooms_ok"))
        }
        plan.invalidate()
    }

    private fun verdictText(v: ZoneModel.Verdict, roomsOk: Boolean): String {
        if (v.emptyP >= 0.5) return "Никого нет"
        val where = if (roomsOk) "«${v.zone}»" else "дома"
        val how = if (v.movingP >= 0.5) "движется" else "спокойно"
        return "Кто-то $where, $how" + if (v.linksLive == 0) " (линий нет)" else ""
    }

    class PlanView(ctx: Context) : View(ctx) {
        var meta: JSONObject? = null
        var verdict: ZoneModel.Verdict? = null
        private val wall = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 5f; color = Color.rgb(148, 163, 184) }
        private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
        private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.WHITE; textSize = 40f; textAlign = Paint.Align.CENTER }
        private val dot = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.rgb(250, 204, 21) }

        override fun onDraw(c: Canvas) {
            val m = meta ?: return
            val rooms = m.optJSONArray("rooms") ?: return
            val polys = (0 until rooms.length()).map { i ->
                val r = rooms.getJSONObject(i); val p = r.getJSONArray("polygon")
                r.getString("name") to (0 until p.length()).map { k -> p.getJSONArray(k).let { doubleArrayOf(it.getDouble(0), it.getDouble(1)) } }
            }
            val pts = polys.flatMap { it.second }
            if (pts.isEmpty()) return
            val x0 = pts.minOf { it[0] }; val x1 = pts.maxOf { it[0] }; val y0 = pts.minOf { it[1] }; val y1 = pts.maxOf { it[1] }
            val s = min((width - 40) / max(x1 - x0, 0.5), (height - 40) / max(y1 - y0, 0.5))
            fun X(x: Double) = (20 + (x - x0) * s).toFloat()
            fun Y(y: Double) = (height - 20 - (y - y0) * s).toFloat()   // север вверх: y плана вверх
            val v = verdict
            val roomsOk = m.optBoolean("rooms_ok")
            val somebody = v != null && v.emptyP < 0.5
            for ((name, poly) in polys) {
                val path = Path().apply { moveTo(X(poly[0][0]), Y(poly[0][1])); for (p in poly.drop(1)) lineTo(X(p[0]), Y(p[1])); close() }
                val here = somebody && (!roomsOk || v!!.zone == name)
                fill.color = if (here) Color.argb(110, 59, 130, 246) else Color.argb(40, 255, 255, 255)
                c.drawPath(path, fill); c.drawPath(path, wall)
                val cx = poly.sumOf { it[0] } / poly.size; val cy = poly.sumOf { it[1] } / poly.size
                if (roomsOk) c.drawText(name, X(cx), Y(cy), text)
            }
            if (!roomsOk) c.drawText("квартира", width / 2f, 60f, text)
            val boards = m.optJSONObject("boards")
            boards?.keys()?.forEach { mac ->
                val xy = boards.getJSONArray(mac)
                c.drawCircle(X(xy.getDouble(0)), Y(xy.getDouble(1)), 14f, dot)
            }
        }
    }
}
