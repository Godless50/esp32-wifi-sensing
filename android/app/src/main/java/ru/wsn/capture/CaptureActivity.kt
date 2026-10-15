package ru.wsn.capture

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.media.AudioManager
import android.media.ToneGenerator
import android.opengl.GLES20
import android.opengl.GLSurfaceView
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.view.WindowManager
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

/**
 * Marking the flat and recording the scenario (AR: the phone knows where it is).
 * Phases: start the recording -> mark every board and access point -> corners of the rooms and doors ->
 * the guided scenario -> "Сохранить". Poses (30 Hz) and events go to /ws/capture (CaptureClient).
 */
class CaptureActivity : AppCompatActivity(), GLSurfaceView.Renderer {
    private enum class Phase { START, OUT1, DEVICES, ROOMS, SCENARIO, DONE }   // OUT1 — «все за дверь» в начале (телефон-центр)

    private val main = Handler(Looper.getMainLooper())
    private lateinit var glView: GLSurfaceView
    private lateinit var status: TextView
    private lateinit var logView: TextView
    private lateinit var cross: View
    private lateinit var panel: LinearLayout
    private var phase = Phase.START

    private var session: Session? = null
    private var installRequested = false
    private val bg = BackgroundRenderer()
    private var viewW = 0
    private var viewH = 0

    @Volatile private var lastPos: FloatArray? = null
    @Volatile private var tracking: TrackingState = TrackingState.STOPPED
    @Volatile private var hitPos: FloatArray? = null
    @Volatile private var floorY: Float? = null
    private var lastPoseMs = 0.0

    private lateinit var client: CaptureClient
    private var transport: CaptureLink? = null
    private var hubMode = false
    private var sessionKey: String? = null
    private val marked = mutableSetOf<String>()
    private val rooms = mutableListOf<String>()
    private var roomOpen: String? = null
    private var roomCorners = 0
    private var lastCorner: FloatArray? = null
    private var doors = 0
    private var runner: ScenarioRunner? = null
    private val logLines = ArrayDeque<String>()
    private var tone: ToneGenerator? = null
    // ---- обход для людей (телефон — центр): игрушечная страница поверх камеры вместо панели ----
    private var hud: WalkHud? = null
    private var preRunner: ScenarioRunner? = null
    private var out1Done = false
    private var naming = false
    private var exitSheet = false
    private var toast: String? = null
    private var toastUntil = 0L
    private var closing = false      // запись закрыта, экран уходит: ничего не начинать заново
    private var startedOnce = false  // сам начинаем запись один раз; дальше — только по «Уходим»

    private fun nowMs(): Double = SystemClock.elapsedRealtimeNanos() / 1e6

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Server.init(this)
        hubMode = !Server.paired()   // без сервера на компьютере центр — этот телефон (HubService)
        if (hubMode && ru.wsn.capture.hub.HubService.current == null) ru.wsn.capture.hub.HubService.start(this)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)  // экран не гаснет во время сценария
        val prefs = getSharedPreferences("wsn", MODE_PRIVATE)
        val cid = prefs.getString("client_id", null) ?: ("android-" + UUID.randomUUID().toString().take(8)).also {
            prefs.edit().putString("client_id", it).apply()
        }
        client = CaptureClient(cid, ::nowMs) { code, msg ->
            log(ERRORS[code]?.let { if (it.endsWith(": ")) it + msg else it } ?: msg, true)
            if (code == "bad_polygon") main.post {  // сервер не принял «Закрыть»: комната снова открыта, углы те же
                rooms.lastOrNull()?.let { r -> rooms.remove(r); roomOpen = r }
                rebuildPanel()
            }
        }
        client.sink = CaptureClient.Sink { transport?.send(it) }
        tone = try { ToneGenerator(AudioManager.STREAM_ALARM, 90) } catch (e: Exception) { null }
        buildUi()
        val onOpen = { client.onOpen(); client.resendEvents(); log(if (hubMode) "Центр в телефоне на связи." else "Связь с сервером есть.") }
        val onMsg = { m: String -> client.onMessage(m); render() }
        val onClosed = { reason: String -> if (client.online) log("Нет связи: $reason", true); client.onClosed(); render() }
        transport = (if (hubMode) LocalLink(cid, onOpen, onMsg, onClosed) else Transport("/ws/capture", onOpen, onMsg, onClosed))
            .also { it.connect() }
        main.post(object : Runnable { override fun run() { client.flushPoses(); main.postDelayed(this, 200) } })
        main.post(object : Runnable { override fun run() { if (client.online) { client.ping(); client.resendEvents() }; main.postDelayed(this, 5000) } })
        main.post(object : Runnable { override fun run() { tickScenario(); main.postDelayed(this, 1000) } })
        main.post(object : Runnable { override fun run() { render(); main.postDelayed(this, 500) } })
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
                s.configure(Config(s).apply {
                    planeFindingMode = Config.PlaneFindingMode.HORIZONTAL
                    updateMode = Config.UpdateMode.LATEST_CAMERA_IMAGE
                    depthMode = Config.DepthMode.DISABLED
                    lightEstimationMode = Config.LightEstimationMode.DISABLED
                    focusMode = Config.FocusMode.AUTO
                })
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
        val configs = s.getSupportedCameraConfigs(CameraConfigFilter(s).setFacingDirection(CameraConfig.FacingDirection.BACK))
        val opts = configs.mapIndexed { i, c ->
            CameraConfigPicker.Option(i, c.imageSize.width, c.imageSize.height, c.fpsRange.upper,
                c.facingDirection == CameraConfig.FacingDirection.BACK)
        }
        CameraConfigPicker.pick(opts)?.let { s.cameraConfig = configs[it.index] }
    }

    override fun onPause() {
        super.onPause()
        if (this::glView.isInitialized) glView.onPause()
        session?.pause()
    }

    override fun onDestroy() {
        super.onDestroy()
        main.removeCallbacksAndMessages(null)  // циклы опроса и сценария не держат закрытый экран
        if (hubMode && !closing && recording()) { client.flushPoses(); client.event("session_stop") }  // брошенная запись не пишется вечно
        transport?.close()
        hud?.destroy()
        session?.close()
        session = null
        tone?.release()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (grantResults.firstOrNull() != PackageManager.PERMISSION_GRANTED) {
            if (hubMode) { finishWalk(false); return }   // приложение покажет «Без камеры не отмечу углы»
            log("Без камеры разметка невозможна.", true)
        }
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
                lastPos = p
                val t = nowMs()
                if (t - lastPoseMs >= 1000.0 / POSE_HZ - 1) {
                    lastPoseMs = t
                    main.post { client.addPose(t, p, q) }
                }
                // пол — самая низкая горизонтальная плоскость (столы и диван выше)
                var fy: Float? = null
                for (pl in s.getAllTrackables(Plane::class.java)) {
                    if (pl.trackingState == TrackingState.TRACKING && pl.type == Plane.Type.HORIZONTAL_UPWARD_FACING &&
                        pl.extentX * pl.extentZ >= 0.25f && p[1] - pl.centerPose.ty() > 0.5f) {
                        val y = pl.centerPose.ty()
                        if (fy == null || y < fy) fy = y
                    }
                }
                floorY = fy
                // луч прицела до бесконечной плоскости пола: у стен и в углах ARCore пол не размечает
                hitPos = fy?.let { floorRayHit(cam, it) }
            }
        } catch (e: Exception) {
            main.post { log("AR: ${e.javaClass.simpleName}", true) }
        }
    }

    private val projM = FloatArray(16)
    private val viewM = FloatArray(16)
    private val pvM = FloatArray(16)
    private val invM = FloatArray(16)

    /** Точка пересечения луча через прицел (центр по X, 45 % высоты) с плоскостью y = floorY, или null. */
    private fun floorRayHit(cam: com.google.ar.core.Camera, floorY: Float): FloatArray? {
        cam.getProjectionMatrix(projM, 0, 0.05f, 100f)
        cam.getViewMatrix(viewM, 0)
        android.opengl.Matrix.multiplyMM(pvM, 0, projM, 0, viewM, 0)
        if (!android.opengl.Matrix.invertM(invM, 0, pvM, 0)) return null
        val ndcY = 1f - 2f * 0.45f
        val near = unproject(0f, ndcY, -1f)
        val far = unproject(0f, ndcY, 1f)
        val dx = far[0] - near[0]; val dy = far[1] - near[1]; val dz = far[2] - near[2]
        if (dy > -1e-4f) return null
        val t = (floorY - near[1]) / dy
        val hx = near[0] + dx * t; val hz = near[2] + dz * t
        val o = cam.pose.translation
        if (kotlin.math.hypot(hx - o[0], hz - o[2]) > MAX_HIT_M) return null
        return floatArrayOf(hx, floorY, hz)
    }

    private fun unproject(x: Float, y: Float, z: Float): FloatArray {
        val r = FloatArray(4)
        android.opengl.Matrix.multiplyMV(r, 0, invM, 0, floatArrayOf(x, y, z, 1f), 0)
        return floatArrayOf(r[0] / r[3], r[1] / r[3], r[2] / r[3])
    }

    // ---------------- действия ----------------
    private fun pose(): JSONArray? {
        val p = lastPos
        if (p == null || tracking != TrackingState.TRACKING) { log("Нет трекинга — медленно поводите телефоном.", true); return null }
        return JSONArray(p.map { it.toDouble() })
    }

    private fun recording(): Boolean = client.session != null && client.owner

    private fun startRecording() {
        if (client.session != null && !client.owner) { log("Запись уже ведёт другой телефон.", true); return }
        client.event("session_start", JSONObject().put("session_kind", "walk"))
        if (hubMode) { startedOnce = true; startOut1(); return }
        setPhase(Phase.DEVICES)
        log("Запись идёт. Отметьте каждую плату и точку доступа.")
    }

    private fun markDevice(d: CaptureClient.Device) {
        val p = pose() ?: return
        client.event("device", JSONObject().put("mac", d.mac).put("role", d.role).put("p", p))
        marked += d.mac
        log("Отмечено: ${d.name}.")
        rebuildPanel()
    }

    private fun newRoom(name: String) {
        val n = name.trim().ifEmpty { "Комната ${rooms.size + 1}" }
        if (n == roomOpen) { log("«$n» уже открыта — отмечайте углы.", true); return }
        if (roomOpen != null) closeRoom()
        client.event("room_new", JSONObject().put("name", n)); roomOpen = n; roomCorners = 0; lastCorner = null
        log("«$n»: наведите кружок на угол у пола и нажмите «Угол» — все углы по кругу.")
        rebuildPanel()
    }

    private fun corner() {
        if (roomOpen == null) { log("Сначала «Новая комната».", true); return }
        val h = hitPos ?: run {
            log(if (floorY == null) "Пол не найден: поводите телефоном над полом посреди комнаты." else
                "Наведите кружок на стык пола и стены ближе 4 м — он станет белым.", true)
            return
        }
        val xz = floatArrayOf(h[0], h[2])
        lastCorner?.let {
            if (kotlin.math.hypot(xz[0] - it[0], xz[1] - it[1]) < MIN_CORNER_GAP_M) { log("Этот угол уже отмечен.", true); return }
        }
        lastCorner = xz
        client.event("corner", JSONObject().put("p", JSONArray(h.map { it.toDouble() })).put("source", "hit"))
        roomCorners++
        log("Угол $roomCorners.")
        rebuildPanel()
    }

    private fun closeRoom() {
        val r = roomOpen ?: return
        if (roomCorners < 3) { log("У «$r» меньше 3 углов — отметьте остальные.", true); return }
        client.event("room_close"); roomOpen = null
        if (r !in rooms) rooms += r
        log("«$r» закрыта ($roomCorners угл.). Дверь отметьте «Дверь» в проёме.")
        rebuildPanel()
    }

    private fun mark(type: String) {
        val h = hitPos
        val p = if (h != null) JSONArray(h.map { it.toDouble() }) else (pose() ?: return)
        client.event("object", JSONObject().put("p", p).put("type", type).put("size", JSONArray(SIZES[type]!!.toList())))
        if (type == "дверь" || type == "арка") doors++
        log("Отмечено: $type.")
        rebuildPanel()
    }

    private fun startScenario() {
        if (roomOpen != null) closeRoom()
        if (rooms.isEmpty()) { log("Сначала отметьте хотя бы одну комнату.", true); return }
        // телефон-центр: «все за дверь» в начале уже было (OUT1); шаги «за дверь» и следующий за ним ждут человека
        val steps = if (hubMode) Scenario.build(rooms, phoneStays = true).drop(1) else Scenario.build(rooms)
        runner = if (hubMode) ScenarioRunner(steps, onStep = { st, i -> sendStep(st, i); beep(false); pushHud() },
            onDone = { sendBack(); beep(true); setPhase(Phase.DONE) },
            gate = { st, prev -> st.activity == "empty" || prev?.activity == "empty" },
            onWait = { st, i -> if (st.activity != "empty") sendBack(); beep(true); pushHud() })
        else ScenarioRunner(steps, onStep = { st, i -> sendStep(st, i); beep(i > 0); rebuildPanel() },
            onDone = { beep(true); setPhase(Phase.DONE) })
        setPhase(Phase.SCENARIO)
        runner?.start()
        log("Сценарий: ${steps.size} шагов, ~${(Scenario.totalSeconds(steps) + 59) / 60} мин. Следуйте подсказкам.")
    }

    private fun tickScenario() {
        if (phase == Phase.OUT1) { preRunner?.tick(); pushHud(); return }
        val r = runner ?: return
        if (phase != Phase.SCENARIO) return
        r.tick()
        if (!r.done) renderStep()
    }

    private fun beep(long: Boolean) {
        try { tone?.startTone(if (long) ToneGenerator.TONE_CDMA_ALERT_CALL_GUARD else ToneGenerator.TONE_PROP_BEEP, if (long) 600 else 200) } catch (_: Exception) {}
    }

    private fun save() {
        if (!recording()) { finish(); return }
        if (marked.none { m -> client.devices.any { it.mac == m && it.role == "board" } })
            log("Ни одна плата не отмечена — план не построится.", true)
        client.event("session_stop")
        runner = null
        log("Запись сохранена. На компьютере запустите ./train.sh")
        setPhase(Phase.START)
        AlertDialog.Builder(this).setTitle("Запись сохранена")
            .setMessage("Теперь на компьютере: ./train.sh — обучение модели под вашу квартиру. Когда закончится, откройте «Карту».")
            .setPositiveButton("Готово") { _, _ -> finish() }.show()
    }

    private fun askSave() {
        AlertDialog.Builder(this).setTitle("Сохранить запись?")
            .setMessage(if (phase == Phase.SCENARIO) "Сценарий не закончен: модель узнает только пройденные шаги." else "Запись остановится и уйдёт на сервер.")
            .setPositiveButton("Сохранить") { _, _ -> save() }.setNegativeButton("Продолжить", null).show()
    }

    // ---------------- интерфейс ----------------
    private fun log(text: String, err: Boolean = false) {
        main.post {
            if (err && hubMode) { toast = friendly(text); toastUntil = System.currentTimeMillis() + 3500; pushHud() }
            logLines.addFirst((if (err) "⚠ " else "") + text)
            while (logLines.size > 3) logLines.removeLast()
            if (this::logView.isInitialized) logView.text = logLines.joinToString("\n")
        }
    }

    private fun setPhase(p: Phase) { phase = p; rebuildPanel(); pushHud() }

    private fun render() {
        if (!this::status.isInitialized || closing) return
        val s = client.session
        val key = s?.optString("id")
        if (key != sessionKey) {
            sessionKey = key
            // вернулись к своей записи; телефон-центр её закрывает и начинает обход с начала (старое «за дверь» не годится)
            if (s != null && client.owner && phase == Phase.START) { if (hubMode) client.event("session_stop") else setPhase(Phase.DEVICES) }
            if (s == null && phase != Phase.START) { runner = null; setPhase(Phase.START) }
        }
        val rec = when {
            s == null -> "не пишем"
            !client.owner -> "пишет другой телефон"
            else -> "● ЗАПИСЬ " + fmt((System.currentTimeMillis() / 1000.0 - s.optDouble("started_wall", 0.0)).toInt())
        }
        val tr = when (tracking) { TrackingState.TRACKING -> "трекинг ок"; TrackingState.PAUSED -> "поводите телефоном"; else -> "трекинга нет" }
        val boards = client.devices.count { it.role == "board" }
        status.text = "${if (client.online) (if (client.clockSynced) "онлайн" else "синхронизация…") else "нет связи"} · $rec · " +
            "плат в эфире $boards · CSI ${client.csiHz.roundToInt()} Гц\n$tr" +
            (roomOpen?.let { " · «$it»: углов $roomCorners" } ?: "") + " · очередь ${client.pendingCount()}"
        cross.background = ring(if (hitPos != null) Color.WHITE else Color.rgb(224, 164, 58))
        cross.visibility = if (phase == Phase.ROOMS) View.VISIBLE else View.INVISIBLE
        // список устройств приходит с сервера: панель — только когда он изменился (иначе закрылся бы выпадающий список)
        if (phase == Phase.DEVICES && client.devices.map { it.mac } != shownDevices) rebuildPanel()
        if (hubMode) {
            if (!startedOnce && phase == Phase.START && client.online && client.session == null) startRecording()
            pushHud()
        }
    }

    private fun fmt(sec: Int): String { val t = sec.coerceAtLeast(0); return "%d:%02d".format(t / 60, t % 60) }

    private fun ring(color: Int) = GradientDrawable().apply { shape = GradientDrawable.OVAL; setStroke(5, color) }

    private fun btn(label: String, kind: Int = 0, onClick: () -> Unit) = Button(this).apply {
        text = label; isAllCaps = false; textSize = 14f; setTextColor(Color.WHITE)
        background = GradientDrawable().apply {
            cornerRadius = 24f
            setColor(when (kind) { 1 -> Color.rgb(59, 130, 246); 2 -> Color.rgb(224, 86, 79); else -> Color.argb(60, 255, 255, 255) })
        }
        setOnClickListener { onClick() }
    }

    private fun row(vararg views: View) = LinearLayout(this).apply {
        orientation = LinearLayout.HORIZONTAL
        views.forEach { addView(it, LinearLayout.LayoutParams(0, 120, 1f).apply { setMargins(6, 6, 6, 6) }) }
    }

    private fun hint(text: String, big: Boolean = false) = TextView(this).apply {
        this.text = text; setTextColor(Color.WHITE); textSize = if (big) 20f else 14f
        if (big) typeface = Typeface.DEFAULT_BOLD
        setPadding(12, 8, 12, 8)
    }

    private fun spinner(items: List<String>, sel: Int = 0) = Spinner(this).apply {
        adapter = ArrayAdapter(this@CaptureActivity, android.R.layout.simple_spinner_dropdown_item, items)
        setBackgroundColor(Color.argb(60, 255, 255, 255))
        if (sel in items.indices) setSelection(sel)
    }

    private var devSel = 0
    private var stepText: TextView? = null
    private var stepInfo: TextView? = null
    private var shownDevices: List<String> = emptyList()

    private fun rebuildPanel() {
        if (!this::panel.isInitialized) return
        panel.removeAllViews()
        stepText = null; stepInfo = null
        when (phase) {
            Phase.OUT1 -> {}
            Phase.START -> {
                panel.addView(hint("1. Начните запись. Пока она идёт, в квартире должны быть только вы."))
                panel.addView(row(btn("Начать запись", 1) { startRecording() }, btn("Закрыть") { finish() }))
            }
            Phase.DEVICES -> {
                val devs = client.devices
                shownDevices = devs.map { it.mac }
                val boards = devs.filter { it.role == "board" }
                panel.addView(hint("2. Поднесите телефон вплотную к устройству и нажмите «Отметить». " +
                    "Отмечено плат ${boards.count { it.mac in marked }} из ${boards.size}, точек доступа " +
                    "${devs.count { it.role == "ap" && it.mac in marked }} из ${devs.count { it.role == "ap" }}." +
                    if (devs.isEmpty()) "\nСервер не видит плат: включены ли они?" else ""))
                if (devs.isNotEmpty()) {
                    val sp = spinner(devs.map { (if (it.mac in marked) "✓ " else "") + it.name }, devSel.coerceAtMost(devs.size - 1))
                    sp.onItemSelectedListener = object : android.widget.AdapterView.OnItemSelectedListener {
                        override fun onItemSelected(p: android.widget.AdapterView<*>?, v: View?, pos: Int, id: Long) { devSel = pos }
                        override fun onNothingSelected(p: android.widget.AdapterView<*>?) {}
                    }
                    panel.addView(row(sp, btn("Отметить", 1) { devs.getOrNull(devSel)?.let { markDevice(it) } }))
                }
                panel.addView(row(btn("Сохранить", 2) { askSave() }, btn("Далее: комнаты →") {
                    if (boards.none { it.mac in marked }) log("Отметьте хотя бы одну плату — от неё строится план.", true)
                    else setPhase(Phase.ROOMS)
                }))
            }
            Phase.ROOMS -> {
                panel.addView(hint("3. Имя комнаты → «Новая комната». Кружок на стык пола и стены → «Угол», все углы по кругу → " +
                    "«Закрыть». В каждом дверном проёме — «Дверь». Комнат: ${rooms.size}, дверей: $doors."))
                val name = EditText(this).apply {
                    hint = "Имя комнаты"; setTextColor(Color.WHITE); setHintTextColor(Color.LTGRAY); textSize = 14f; isSingleLine = true
                }
                panel.addView(row(name, btn("Новая комната") { newRoom(name.text.toString()); name.setText("") }))
                panel.addView(row(btn("Угол", 1) { corner() }, btn("Закрыть комнату") { closeRoom() }, btn("Дверь") { mark("дверь") }))
                panel.addView(row(btn("← Устройства") { setPhase(Phase.DEVICES) }, btn("Сохранить", 2) { askSave() },
                    btn("Сценарий →", 1) { startScenario() }))
            }
            Phase.SCENARIO -> {
                stepText = hint("", big = true).also { panel.addView(it) }
                stepInfo = hint("").also { panel.addView(it) }
                val r = runner
                panel.addView(row(btn(if (r?.paused == true) "Продолжить" else "Пауза") { r?.let { it.paused = !it.paused }; rebuildPanel() },
                    btn("Пропустить шаг") { r?.skip() }, btn("Сохранить", 2) { askSave() }))
                renderStep()
            }
            Phase.DONE -> {
                panel.addView(hint("Сценарий пройден. Нажмите «Сохранить» — запись уйдёт на сервер. " +
                    "Потом на компьютере: ./train.sh", big = true))
                panel.addView(row(btn("Сохранить", 1) { save() }))
            }
        }
    }

    private fun renderStep() {
        val r = runner ?: return
        val st = r.current ?: return
        stepText?.text = st.text
        stepInfo?.text = "Шаг ${r.index + 1} из ${r.steps.size} · осталось ${fmt(r.left)}" + if (r.paused) " · пауза" else ""
    }

    private fun buildUi() {
        glView = GLSurfaceView(this).apply {
            preserveEGLContextOnPause = true
            setEGLContextClientVersion(2)
            setEGLConfigChooser(8, 8, 8, 8, 16, 0)
            setRenderer(this@CaptureActivity)
            renderMode = GLSurfaceView.RENDERMODE_CONTINUOUSLY
        }
        val root = FrameLayout(this)
        root.addView(glView, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT))
        status = TextView(this).apply { setTextColor(Color.WHITE); textSize = 13f; setPadding(24, 48, 24, 16); setBackgroundColor(Color.argb(170, 16, 19, 24)) }
        root.addView(status, FrameLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT, Gravity.TOP))
        logView = TextView(this).apply { setTextColor(Color.WHITE); textSize = 13f; setPadding(24, 8, 24, 8); setBackgroundColor(Color.argb(120, 0, 0, 0)) }
        root.addView(logView, FrameLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT, Gravity.TOP).apply { topMargin = 220 })
        cross = View(this).apply { background = ring(Color.WHITE) }
        root.addView(cross, FrameLayout.LayoutParams(110, 110, Gravity.TOP or Gravity.CENTER_HORIZONTAL))
        root.post { cross.translationY = root.height * 0.45f - 55 }
        panel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; setPadding(12, 12, 12, 36); setBackgroundColor(Color.argb(200, 16, 19, 24))
        }
        root.addView(panel, FrameLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT, Gravity.BOTTOM))
        if (hubMode) {
            for (v in listOf(status, logView, cross, panel)) v.visibility = View.GONE
            cross.alpha = 0f
            hud = WalkHud(this) { a, args -> hudAction(a, args) }.also { root.addView(it.view, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT)) }
        }
        setContentView(root)
        rebuildPanel()
        render()
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (hubMode) { hudAction("walkBack", JSONObject()); return }
        if (recording()) askSave() else @Suppress("DEPRECATION") super.onBackPressed()
    }

    // ================= обход для людей (телефон — центр) =================
    private fun sendStep(st: Step, i: Int) {
        client.event("step", JSONObject().put("name", st.name).put("activity", st.activity)
            .put("room", st.room ?: JSONObject.NULL).put("duration_s", st.seconds).put("index", i))
        if (st.activity in POSES) client.event("label", JSONObject().put("pose", st.activity))
    }

    /** «За дверь» закончилось: метка «никого» обучения кончается здесь, дальше — возвращение (без метки). */
    private fun sendBack() {
        client.event("step", JSONObject().put("name", "back").put("activity", "back").put("room", JSONObject.NULL)
            .put("duration_s", 0).put("index", -1))
    }

    private fun startOut1() {
        out1Done = false
        preRunner = ScenarioRunner(listOf(Scenario.leave(1, phoneStays = true)), onStep = { st, i -> sendStep(st, i); pushHud() },
            onDone = { sendBack(); beep(true); out1Done = true; pushHud() },
            gate = { st, _ -> st.activity == "empty" })
        setPhase(Phase.OUT1)
        preRunner?.start()
    }

    /** Кто-то остался дома: эта попытка «за дверь» не считается, ждём «Уходим» снова. */
    private fun outAgain(r: ScenarioRunner) {
        client.event("step", JSONObject().put("name", "redo").put("activity", "redo").put("room", JSONObject.NULL)
            .put("duration_s", 0).put("index", -1))
        r.again()
    }

    private fun friendly(text: String): String = when {
        text.startsWith("Нет трекинга") -> "Поводи телефоном"
        text.startsWith("Пол не найден") -> "Покажи мне пол"
        text.startsWith("Наведите кружок") -> "Ближе к углу у пола"
        text.startsWith("Этот угол уже") -> "Тут уже отмечено"
        text.contains("меньше 3 углов") -> "Нужно хотя бы 3 угла"
        text.startsWith("Ни одна плата") || text.startsWith("Сервер не видит") -> "Жучок молчит"
        text.startsWith("Комната не замкнута") -> "Углы не сходятся — эту комнату заново"
        text.startsWith("ARCore") || text.startsWith("Камера недоступна") -> "Камера не запустилась"
        text.startsWith("Нет связи") -> "Секунду…"
        else -> text
    }

    private fun hudAction(a: String, args: JSONObject) {
        val r = runner
        when (a) {
            "walkGo" -> when (phase) {
                Phase.START -> if (client.online && client.session == null) { startRecording(); preRunner?.go() }
                Phase.OUT1 -> if (out1Done) { setPhase(Phase.DEVICES) } else preRunner?.go()
                Phase.SCENARIO -> r?.go()
                else -> {}
            }
            "walkAgain" -> when (phase) {
                Phase.OUT1 -> preRunner?.let { outAgain(it) }
                Phase.SCENARIO -> r?.let { outAgain(it) }
                else -> {}
            }
            "walkBoard" -> {
                val boards = client.devices.filter { it.role == "board" }
                boards.firstOrNull { it.mac !in marked }?.let { markDevice(it) }
                if (boards.isNotEmpty() && boards.all { it.mac in marked }) { naming = true; setPhase(Phase.ROOMS) }
            }
            "walkRoom" -> {
                val name = args.optString("name").trim().take(24)
                if (name.isEmpty()) return
                if (rooms.any { it.equals(name, true) } || roomOpen?.equals(name, true) == true) { log("Такая комната уже есть", true); return }
                if (roomOpen != null && roomCorners == 0) roomOpen = null   // пустую комнату просто переименовали
                newRoom(name); naming = roomOpen == null
            }
            "walkCorner" -> corner()
            "walkRoomDone" -> { closeRoom(); if (roomOpen == null) naming = true }
            "walkAddRoom" -> {
                if (roomOpen != null && roomCorners > 0) closeRoom()
                if (roomOpen == null || roomCorners == 0) naming = true
            }
            "walkScenario" -> {
                if (roomOpen != null && roomCorners == 0) roomOpen = null   // открыли «+» и передумали
                if (roomOpen == null || roomCorners >= 3) { naming = false; startScenario() }
            }
            "walkPause" -> r?.paused = true
            "walkResume" -> { exitSheet = false; r?.paused = false }
            "walkRedo" -> { r?.paused = false; r?.again() }
            "walkSkip" -> r?.let { rr ->   // вся комната: походи, постой, посиди
                rr.paused = false
                val room = rr.current?.room
                do rr.skip() while (room != null && !rr.done && !rr.waiting && rr.current?.room == room)
            }
            "walkExit" -> finishWalk(false)
            "walkSave" -> finishWalk(true)
            "walkBack" -> when {
                phase == Phase.DONE -> {}
                phase == Phase.SCENARIO && r != null && !r.waiting && r.current?.activity != "empty" -> r.paused = true
                phase == Phase.ROOMS && naming && (rooms.isNotEmpty() || roomOpen != null) && roomOpen != null -> naming = false
                else -> exitSheet = !exitSheet
            }
        }
        pushHud()
    }

    /** Конец записи: полный обход — приложение начнёт учиться; брошенный — запись остаётся, обход начинают заново. */
    private fun finishWalk(complete: Boolean) {
        if (closing) return
        closing = true
        if (recording()) { client.flushPoses(); client.event("session_stop") }
        getSharedPreferences("wsn-ui", MODE_PRIVATE).edit().putBoolean("walk_complete", complete).apply()
        runner = null; preRunner = null
        transport?.close(); transport = null   // ответы центра больше не нужны: иначе экран начал бы новую запись
        finish()
    }

    private fun pushHud() {
        val h = hud ?: return
        if (toast != null && System.currentTimeMillis() > toastUntil) toast = null
        h.push(hudState())
    }

    private fun hudState(): JSONObject {
        val w = JSONObject()
        toast?.let { w.put("msg", it) }
        val r = runner
        val roomList = JSONArray()
        val active: String? = when (phase) {
            Phase.ROOMS -> roomOpen
            Phase.SCENARIO -> r?.current?.room
            else -> null
        }
        val all = rooms + listOfNotNull(roomOpen?.takeIf { it !in rooms })
        val activeIdx = active?.let { rooms.indexOf(it) } ?: -1
        val tourOrLater = phase == Phase.SCENARIO && r != null && r.steps.take(r.index + 1).any { it.name == "tour" }
        for ((i, n) in all.withIndex()) roomList.put(JSONObject().put("name", n).put("done",
            n != active && (phase == Phase.ROOMS && n in rooms || phase == Phase.SCENARIO && (tourOrLater || activeIdx >= 0 && i < activeIdx) || phase == Phase.DONE)))
        w.put("rooms", roomList)
        active?.let { w.put("active", it) }
        val outTotal = Scenario.EMPTY_S
        val screen: String
        var part = 0.0
        when (phase) {
            Phase.START -> { screen = if (client.online) "walk_out" else "walk_wait"; w.put("out", JSONObject().put("going", false).put("left", outTotal).put("total", outTotal)) }
            Phase.OUT1 -> {
                val p = preRunner
                part = 0.1 * (if (out1Done) 1.0 else if (p == null || p.waiting) 0.0 else 1.0 - p.left / outTotal.toDouble())
                screen = if (out1Done) "walk_back" else "walk_out"
                w.put("out", JSONObject().put("going", p != null && !p.waiting).put("left", p?.left ?: outTotal).put("total", outTotal))
            }
            Phase.DEVICES -> {
                part = 0.12
                screen = "walk_find"
                val boards = client.devices.filter { it.role == "board" }
                w.put("boardsTotal", boards.size)
                boards.withIndex().firstOrNull { it.value.mac !in marked }?.let { (i, b) -> w.put("board", JSONObject().put("name", b.name).put("i", i + 1)) }
            }
            Phase.ROOMS -> {
                part = (0.15 + 0.05 * rooms.size).coerceAtMost(0.3)
                if (naming || roomOpen == null) screen = "walk_room" else {
                    screen = "walk_mark"
                    w.put("corners", roomCorners)
                    w.put("aim", when { tracking != TrackingState.TRACKING -> "lost"; floorY == null -> "nofloor"; hitPos == null -> "far"; else -> "ok" })
                }
            }
            Phase.SCENARIO -> {
                val st = r?.current
                if (r != null && st != null) {
                    val total = Scenario.totalSeconds(r.steps).toDouble()
                    val doneS = r.steps.take(r.index).sumOf { it.seconds } + if (r.waiting) 0 else st.seconds - r.left
                    part = 0.3 + 0.7 * (doneS / total)
                }
                screen = if (r == null || st == null) "walk_wait" else when {
                    st.activity == "empty" -> { w.put("out", JSONObject().put("going", !r.waiting).put("left", r.left).put("total", st.seconds)); "walk_out" }
                    r.waiting -> { w.put("out", JSONObject().put("total", outTotal)); "walk_back" }
                    r.paused -> { w.put("pause", "task"); "walk_pause" }
                    else -> {
                        val kind = when { st.name == "tour" -> "tour"; st.name.startsWith("walk2:") -> "walk2"; else -> st.activity }
                        w.put("task", JSONObject().put("kind", kind).put("room", st.room ?: JSONObject.NULL).put("left", r.left).put("total", st.seconds))
                        "walk_task"
                    }
                }
            }
            Phase.DONE -> { part = 1.0; screen = "walk_back"; w.put("last", true).put("out", JSONObject().put("total", outTotal)) }
        }
        w.put("part", part)
        val out = if (exitSheet && phase != Phase.DONE) { w.put("pause", "exit"); "walk_pause" } else screen
        return JSONObject().put("screen", out).put("walk", w)
    }

    companion object {
        const val POSE_HZ = 30
        const val MAX_HIT_M = 4.0f
        const val MIN_CORNER_GAP_M = 0.25f
        val POSES = setOf("stand", "sit", "lie", "walk")
        val SIZES = mapOf("дверь" to doubleArrayOf(0.9, 0.1, 2.0), "арка" to doubleArrayOf(1.0, 0.1, 2.2))
        val ERRORS = mapOf(
            "session_busy" to "Уже идёт запись с другого телефона.", "not_owner" to "Запись ведёт другой телефон.",
            "no_session" to "Запись не идёт.", "no_clock_sync" to "Синхронизирую время, повторите через секунду.",
            "no_device" to "Ни одна плата не передаёт данные — проверьте их питание и Wi-Fi.",
            "bad_polygon" to "Комната не замкнута: ", "bad_device" to "Отметка не принята: ",
            "no_room" to "Сначала «Новая комната».", "bad_session" to "Не удалось начать запись: ",
        )
    }
}
