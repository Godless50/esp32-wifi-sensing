package ru.wsn.capture

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.opengl.GLES20
import android.opengl.GLSurfaceView
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.Spinner
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.google.ar.core.ArCoreApk
import com.google.ar.core.CameraConfig
import com.google.ar.core.CameraConfigFilter
import com.google.ar.core.Config
import com.google.ar.core.Plane
import com.google.ar.core.Session
import com.google.ar.core.TrackingState
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID
import javax.microedition.khronos.egl.EGLConfig
import javax.microedition.khronos.opengles.GL10
import kotlin.math.roundToInt

class MainActivity : AppCompatActivity(), GLSurfaceView.Renderer {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var prefs: android.content.SharedPreferences
    private lateinit var glView: GLSurfaceView
    private lateinit var status: TextView
    private lateinit var logView: TextView
    private lateinit var cross: View
    private val needRec = mutableListOf<View>()
    private lateinit var bWalk: Button
    private lateinit var bEmpty: Button
    private lateinit var bStop: Button
    private lateinit var durSpinner: Spinner
    private lateinit var roomName: EditText
    private lateinit var objType: Spinner
    private val poseButtons = mutableListOf<Button>()

    private var session: Session? = null
    private var installRequested = false
    private val bg = BackgroundRenderer()
    private var viewW = 0
    private var viewH = 0
    private var cameraInfo = "камера: —"

    @Volatile private var lastPos: FloatArray? = null
    @Volatile private var lastRot: FloatArray? = null
    @Volatile private var tracking: TrackingState = TrackingState.STOPPED
    @Volatile private var hitPos: FloatArray? = null
    private var lastPoseMs = 0.0

    private lateinit var client: CaptureClient
    private var transport: Transport? = null
    private var roomOpen: String? = null
    private var roomCorners = 0
    private var lastCorner: FloatArray? = null
    private val logLines = ArrayDeque<String>()

    private fun nowMs(): Double = SystemClock.elapsedRealtimeNanos() / 1e6

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = getSharedPreferences("wsn", Context.MODE_PRIVATE)
        val cid = prefs.getString("client_id", null) ?: ("android-" + UUID.randomUUID().toString().take(8)).also {
            prefs.edit().putString("client_id", it).apply()
        }
        client = CaptureClient(cid, ::nowMs) { code, msg -> log(ERRORS[code]?.let { if (it.endsWith(": ")) it + msg else it } ?: msg, true) }
        client.sink = CaptureClient.Sink { transport?.send(it) }
        buildUi()
        if (prefs.getString("password", "").isNullOrEmpty()) askSettings() else connect()
        main.post(object : Runnable { override fun run() { client.flushPoses(); main.postDelayed(this, 200) } })
        main.post(object : Runnable { override fun run() { if (client.online) { client.ping(); client.resendEvents() }; main.postDelayed(this, 5000) } })
        main.post(object : Runnable { override fun run() { render(); main.postDelayed(this, 500) } })
    }

    // ---------------- network ----------------
    private fun connect() {
        transport?.close()
        val url = prefs.getString("url", DEFAULT_URL)!!
        transport = Transport(url, "wsn", prefs.getString("password", "")!!,
            onOpen = { client.onOpen(); client.resendEvents(); log("Связь с сервером есть.") },
            onMessage = { client.onMessage(it); render() },
            onClosed = { reason -> if (client.online) log("Нет связи: $reason", true); client.onClosed(); render() },
        ).also { it.connect() }
    }

    private fun askSettings() {
        val box = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(48, 24, 48, 0) }
        val url = EditText(this).apply { setText(prefs.getString("url", DEFAULT_URL)); hint = "wss://…/ws/capture" }
        val pass = EditText(this).apply {
            hint = "пароль (data/ui_password)"; inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            setText(prefs.getString("password", ""))
        }
        box.addView(url); box.addView(pass)
        AlertDialog.Builder(this).setTitle("Сервер").setView(box).setCancelable(false)
            .setPositiveButton("Сохранить") { _, _ ->
                prefs.edit().putString("url", url.text.toString().trim()).putString("password", pass.text.toString()).apply()
                connect()
            }.show()
    }

    // ---------------- AR lifecycle ----------------
    override fun onResume() {
        super.onResume()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.CAMERA), 1)
            return
        }
        if (session == null) {
            try {
                when (ArCoreApk.getInstance().requestInstall(this, !installRequested)) {
                    ArCoreApk.InstallStatus.INSTALL_REQUESTED -> { installRequested = true; return }
                    else -> {}
                }
                val s = Session(this)
                pickCameraConfig(s)
                val cfg = Config(s).apply {
                    planeFindingMode = Config.PlaneFindingMode.HORIZONTAL
                    updateMode = Config.UpdateMode.LATEST_CAMERA_IMAGE
                    depthMode = Config.DepthMode.DISABLED
                    lightEstimationMode = Config.LightEstimationMode.DISABLED
                    focusMode = Config.FocusMode.AUTO
                }
                s.configure(cfg)
                session = s
            } catch (e: Exception) {
                log("ARCore не запустился: ${e.javaClass.simpleName}: ${e.message}", true)
                return
            }
        }
        try { session?.resume() } catch (e: Exception) { log("Камера недоступна: ${e.message}", true); return }
        glView.onResume()
    }

    private fun pickCameraConfig(s: Session) {
        val filter = CameraConfigFilter(s).setFacingDirection(CameraConfig.FacingDirection.BACK)
        val configs = s.getSupportedCameraConfigs(filter)
        val opts = configs.mapIndexed { i, c ->
            CameraConfigPicker.Option(i, c.imageSize.width, c.imageSize.height, c.fpsRange.upper,
                c.facingDirection == CameraConfig.FacingDirection.BACK)
        }
        val pick = CameraConfigPicker.pick(opts) ?: return
        val chosen = configs[pick.index]
        s.cameraConfig = chosen
        cameraInfo = "камера: CPU ${chosen.imageSize.width}×${chosen.imageSize.height}, " +
            "GPU ${chosen.textureSize.width}×${chosen.textureSize.height}, ${chosen.fpsRange.upper} FPS (из ${configs.size})"
        log(cameraInfo)
    }

    override fun onPause() {
        super.onPause()
        if (this::glView.isInitialized) glView.onPause()
        session?.pause()
    }

    override fun onDestroy() {
        super.onDestroy()
        transport?.close()
        session?.close()
        session = null
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (grantResults.firstOrNull() != PackageManager.PERMISSION_GRANTED) log("Без камеры съёмка невозможна.", true)
    }

    // ---------------- GL ----------------
    override fun onSurfaceCreated(gl: GL10?, config: EGLConfig?) {
        GLES20.glClearColor(0f, 0f, 0f, 1f)
        bg.createOnGlThread()
    }

    override fun onSurfaceChanged(gl: GL10?, width: Int, height: Int) {
        GLES20.glViewport(0, 0, width, height)
        viewW = width; viewH = height
        @Suppress("DEPRECATION")
        session?.setDisplayGeometry(windowManager.defaultDisplay.rotation, width, height)
    }

    override fun onDrawFrame(gl: GL10?) {
        GLES20.glClear(GLES20.GL_COLOR_BUFFER_BIT or GLES20.GL_DEPTH_BUFFER_BIT)
        val s = session ?: return
        try {
            s.setCameraTextureName(bg.textureId)
            @Suppress("DEPRECATION")
            s.setDisplayGeometry(windowManager.defaultDisplay.rotation, viewW, viewH)
            val frame = s.update()
            bg.draw(frame)
            val cam = frame.camera
            tracking = cam.trackingState
            if (tracking == TrackingState.TRACKING) {
                val pose = cam.pose
                val p = pose.translation
                val q = pose.rotationQuaternion
                lastPos = p; lastRot = q
                val t = nowMs()
                if (t - lastPoseMs >= 1000.0 / POSE_HZ - 1) {
                    lastPoseMs = t
                    main.post { client.addPose(t, p, q) }
                }
                var hp: FloatArray? = null
                for (hit in frame.hitTest(viewW / 2f, viewH * 0.45f)) {
                    val tr = hit.trackable
                    if (tr is Plane && tr.isPoseInPolygon(hit.hitPose) && tr.type == Plane.Type.HORIZONTAL_UPWARD_FACING &&
                        hit.distance <= MAX_HIT_M) {  // дальние точки пола неточны и уводят угол в соседнюю комнату
                        hp = hit.hitPose.translation; break
                    }
                }
                hitPos = hp
            }
        } catch (e: Exception) {
            main.post { log("AR: ${e.javaClass.simpleName}", true) }
        }
    }

    // ---------------- actions ----------------
    private fun needPose(): JSONArray? {
        val p = lastPos
        if (p == null || tracking != TrackingState.TRACKING) { log("Нет трекинга — поводите телефоном.", true); return null }
        return JSONArray(p.map { it.toDouble() })
    }

    private fun onButton(v: View) {
        when (v.tag as String) {
            "walk" -> client.event("session_start", JSONObject().put("session_kind", "walk"))
            "empty" -> {
                val dur = DURATIONS[durSpinner.selectedItemPosition]
                client.event("session_start", JSONObject().put("session_kind", "empty").put("duration_s", dur))
                log("Пустая квартира: ${dur / 60} мин. Уходите, запись остановится сама.")
            }
            "stop" -> client.event("session_stop")
            "origin" -> needPose()?.let { client.event("origin", JSONObject().put("p", it)); log("Плата отмечена.") }
            "router" -> needPose()?.let { client.event("router", JSONObject().put("p", it)); log("Роутер отмечен.") }
            "room" -> {
                val name = roomName.text.toString().trim().ifEmpty { "Комната ${logLines.size}" }
                client.event("room_new", JSONObject().put("name", name)); roomOpen = name; roomCorners = 0; lastCorner = null
                log("Комната «$name»: отмечайте углы на полу.")
            }
            "corner" -> {
                val h = hitPos
                val (p, src) = if (h != null) JSONArray(h.map { it.toDouble() }) to "hit"
                else (needPose()?.also { it.put(1, 0.0) } ?: return) to "phone"
                val xz = floatArrayOf(p.getDouble(0).toFloat(), p.getDouble(2).toFloat())
                lastCorner?.let {
                    if (kotlin.math.hypot(xz[0] - it[0], xz[1] - it[1]) < MIN_CORNER_GAP_M) {
                        log("Этот угол уже отмечен — перейдите к следующему.", true); return
                    }
                }
                lastCorner = xz
                client.event("corner", JSONObject().put("p", p).put("source", src)); roomCorners++
                log("Угол $roomCorners${if (src == "phone") " (по телефону)" else ""}.")
            }
            "close" -> { client.event("room_close"); roomOpen = null }
            "object" -> {
                val h = hitPos
                val p = if (h != null) JSONArray(h.map { it.toDouble() }) else (needPose() ?: return)
                val type = OBJECTS[objType.selectedItemPosition]
                client.event("object", JSONObject().put("p", p).put("type", type).put("size", JSONArray(SIZES[type]!!.toList())))
                log("Предмет: $type.")
            }
        }
        render()
    }

    private fun onPoseLabel(b: Button) {
        client.event("label", JSONObject().put("pose", b.tag as String))
        poseButtons.forEach { it.alpha = if (it === b) 1f else 0.55f }
    }

    // ---------------- UI ----------------
    private fun log(text: String, err: Boolean = false) {
        main.post {
            logLines.addFirst((if (err) "⚠ " else "") + text)
            while (logLines.size > 3) logLines.removeLast()
            if (this::logView.isInitialized) logView.text = logLines.joinToString("\n")
        }
    }

    private fun render() {
        if (!this::status.isInitialized) return
        val s = client.session
        val rec = s != null && client.owner
        val recText = when {
            s == null -> "не пишем"
            !client.owner -> "пишет другой телефон"
            s.optString("kind") == "empty" -> "ПУСТО"
            else -> "● ЗАПИСЬ " + fmtDur(System.currentTimeMillis() / 1000.0 - s.optDouble("started_wall", 0.0))
        }
        val tr = when (tracking) { TrackingState.TRACKING -> "трекинг ок"; TrackingState.PAUSED -> "трекинг: поводите телефоном"; else -> "трекинг: нет" }
        status.text = "${if (client.online) (if (client.clockSynced) "онлайн" else "синхронизация…") else "нет связи"} · $recText · " +
            "CSI ${client.csiHz.roundToInt()} Гц · очередь ${client.pendingCount()}\n$tr · ${roomOpen?.let { "«$it»: углов $roomCorners · " } ?: ""}$cameraInfo"
        cross.background = ring(if (hitPos != null) Color.WHITE else Color.rgb(224, 164, 58))
        bWalk.isEnabled = s == null; bEmpty.isEnabled = s == null; durSpinner.isEnabled = s == null
        bStop.isEnabled = rec
        val walk = rec && s?.optString("kind") == "walk"
        needRec.forEach { it.isEnabled = walk; it.alpha = if (walk) 1f else 0.4f }
    }

    private fun fmtDur(sec: Double): String { val t = sec.toInt().coerceAtLeast(0); return "%d:%02d".format(t / 60, t % 60) }

    private fun ring(color: Int) = GradientDrawable().apply { shape = GradientDrawable.OVAL; setStroke(5, color) }

    private fun btn(label: String, tag: String, rec: Boolean = true, primary: Boolean = false, danger: Boolean = false) =
        Button(this).apply {
            text = label; this.tag = tag; isAllCaps = false; textSize = 14f
            setTextColor(Color.WHITE)
            background = GradientDrawable().apply {
                cornerRadius = 24f
                setColor(when { primary -> Color.rgb(59, 130, 246); danger -> Color.rgb(224, 86, 79); else -> Color.argb(60, 255, 255, 255) })
            }
            setOnClickListener { onButton(it) }
            if (rec) needRec.add(this)
        }

    private fun row(vararg views: View) = LinearLayout(this).apply {
        orientation = LinearLayout.HORIZONTAL
        views.forEach { addView(it, LinearLayout.LayoutParams(0, 120, 1f).apply { setMargins(6, 6, 6, 6) }) }
    }

    private fun spinner(items: List<String>) = Spinner(this).apply {
        adapter = ArrayAdapter(this@MainActivity, android.R.layout.simple_spinner_dropdown_item, items)
        setBackgroundColor(Color.argb(60, 255, 255, 255))
    }

    private fun buildUi() {
        glView = GLSurfaceView(this).apply {
            preserveEGLContextOnPause = true
            setEGLContextClientVersion(2)
            setEGLConfigChooser(8, 8, 8, 8, 16, 0)
            setRenderer(this@MainActivity)
            renderMode = GLSurfaceView.RENDERMODE_CONTINUOUSLY
        }
        val root = FrameLayout(this)
        root.addView(glView, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
        status = TextView(this).apply { setTextColor(Color.WHITE); textSize = 13f; setPadding(24, 48, 24, 16); setBackgroundColor(Color.argb(170, 16, 19, 24)) }
        root.addView(status, FrameLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT, Gravity.TOP))
        logView = TextView(this).apply { setTextColor(Color.WHITE); textSize = 13f; setPadding(24, 8, 24, 8); setBackgroundColor(Color.argb(120, 0, 0, 0)) }
        root.addView(logView, FrameLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT, Gravity.TOP).apply { topMargin = 260 })
        cross = View(this).apply { background = ring(Color.WHITE) }
        root.addView(cross, FrameLayout.LayoutParams(110, 110, Gravity.TOP or Gravity.CENTER_HORIZONTAL))
        root.post { cross.translationY = root.height * 0.45f - 55 }

        bWalk = btn("Запись", "walk", rec = false, primary = true)
        durSpinner = spinner(DURATIONS.map { "${it / 60} мин" }).apply { setSelection(1) }
        bEmpty = btn("Пусто", "empty", rec = false)
        bStop = btn("Стоп", "stop", rec = false, danger = true)
        roomName = EditText(this).apply { hint = "Имя комнаты"; setTextColor(Color.WHITE); setHintTextColor(Color.LTGRAY); textSize = 14f; isSingleLine = true }
        needRec.add(roomName)
        objType = spinner(OBJECTS); needRec.add(objType)
        val labels = listOf("Стою" to "stand", "Сижу" to "sit", "Лежу" to "lie", "Иду" to "walk").map { (l, t) ->
            btn(l, t).apply { setOnClickListener { onPoseLabel(this) }; poseButtons.add(this) }
        }
        val panel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; setPadding(12, 12, 12, 36); setBackgroundColor(Color.argb(190, 16, 19, 24))
            addView(row(bWalk, durSpinner, bEmpty, bStop))
            addView(row(btn("Я у платы", "origin"), btn("Я у роутера", "router")))
            addView(row(roomName, btn("Новая комната", "room"), btn("Закрыть комнату", "close")))
            addView(row(btn("Угол", "corner"), objType, btn("Предмет", "object")))
            addView(row(*labels.toTypedArray()))
        }
        root.addView(panel, FrameLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT, Gravity.BOTTOM))
        status.setOnLongClickListener { askSettings(); true }
        setContentView(root)
        render()
    }

    companion object {
        const val DEFAULT_URL = "wss://192.168.4.77:8443/ws/capture"
        const val POSE_HZ = 30
        const val MAX_HIT_M = 4.0f
        const val MIN_CORNER_GAP_M = 0.25f
        val DURATIONS = listOf(300, 600, 1200, 1800)
        val OBJECTS = listOf("стол", "диван", "кровать", "шкаф", "дверь", "другое")
        val SIZES = mapOf(
            "стол" to doubleArrayOf(1.2, 0.8, 0.75), "диван" to doubleArrayOf(2.0, 0.9, 0.8),
            "кровать" to doubleArrayOf(2.0, 1.6, 0.5), "шкаф" to doubleArrayOf(1.0, 0.6, 2.0),
            "дверь" to doubleArrayOf(0.9, 0.1, 2.0), "другое" to doubleArrayOf(0.5, 0.5, 0.8),
        )
        val ERRORS = mapOf(
            "session_busy" to "Уже идёт запись с другого телефона.", "not_owner" to "Запись ведёт другой телефон.",
            "no_session" to "Запись не идёт.", "no_clock_sync" to "Синхронизирую время, повторите через секунду.",
            "no_device" to "Плата не передаёт CSI — проверьте её.", "bad_polygon" to "Комната не замкнута: ",
            "bad_router" to "Роутер: ", "no_room" to "Сначала «Новая комната».", "bad_session" to "Не удалось начать запись: ",
        )
    }
}
