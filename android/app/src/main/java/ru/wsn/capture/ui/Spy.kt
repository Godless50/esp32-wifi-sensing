package ru.wsn.capture.ui

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.text.InputType
import android.util.TypedValue
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.core.content.res.ResourcesCompat
import androidx.core.graphics.PathParser
import ru.wsn.capture.R

/** «Жучок» look: the tokens and the few components every screen is built from (the same set as the design
 *  canvas, so the gadget frame, cards and buttons are identical everywhere). Views are made in code, like the
 *  rest of the app. Orange is only for things you press; presence is yellow; alert red only for errors. */
object Spy {
    val BG = Color.parseColor("#0E1726"); val SURF = Color.parseColor("#13203A"); val DEEP = Color.parseColor("#0B1424")
    val LINE = Color.parseColor("#22375C"); val TXT = Color.parseColor("#EAF2FF"); val TXT2 = Color.parseColor("#C9D6EA")
    val MUTED = Color.parseColor("#9FB3D1"); val CTRL = Color.parseColor("#56739E"); val SCREW = Color.parseColor("#7D95BD")
    val ACT = Color.parseColor("#FF9F43"); val ACT_INK = Color.parseColor("#1A1206"); val PRES = Color.parseColor("#FFD166")
    val OK = Color.parseColor("#5CFFA6"); val ALERT = Color.parseColor("#FF7A70"); val DIS = Color.parseColor("#1C2E4D")
    val PAPER = Color.parseColor("#F4E9D0"); val PAPER_INK = Color.parseColor("#1B1B1B"); val PAPER_SUB = Color.parseColor("#5A4E3A")
    val TAPE = Color.parseColor("#D8CCAF")

    fun dp(ctx: Context, v: Float) = TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, ctx.resources.displayMetrics)
    fun dpi(ctx: Context, v: Int) = dp(ctx, v.toFloat()).toInt()

    private var head: Typeface? = null; private var body: Typeface? = null; private var bodyBold: Typeface? = null; private var mono: Typeface? = null
    fun head(c: Context) = head ?: (ResourcesCompat.getFont(c, R.font.russo_one) ?: Typeface.DEFAULT_BOLD).also { head = it }
    fun body(c: Context) = body ?: (ResourcesCompat.getFont(c, R.font.plex_sans) ?: Typeface.DEFAULT).also { body = it }
    fun bodyBold(c: Context) = bodyBold ?: (ResourcesCompat.getFont(c, R.font.plex_sans_semibold) ?: Typeface.DEFAULT_BOLD).also { bodyBold = it }
    fun mono(c: Context) = mono ?: (ResourcesCompat.getFont(c, R.font.pt_mono) ?: Typeface.MONOSPACE).also { mono = it }

    // ---------------- page ----------------
    /** A scrolling page; returns the column to fill. A [spring] keeps what follows it at the bottom of the screen. */
    fun page(a: Activity): LinearLayout {
        val col = LinearLayout(a).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dpi(a, 20), dpi(a, 28), dpi(a, 20), dpi(a, 28))
        }
        a.setContentView(ScrollView(a).apply { setBackgroundColor(BG); isFillViewport = true; addView(col, MATCH_PARENT, WRAP_CONTENT) })
        return col
    }

    fun spring(c: Context) = View(c).apply { layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, 0, 1f) }

    /** Add with the standard vertical gap (none above the first child or a spring). */
    fun LinearLayout.add(v: View, top: Int = 14): View {
        val lp = (v.layoutParams as? LinearLayout.LayoutParams) ?: LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT)
        if (childCount > 0 && lp.weight == 0f) lp.topMargin = dpi(context, top)
        addView(v, lp); return v
    }

    // ---------------- text ----------------
    fun text(c: Context, s: CharSequence, size: Float = 15f, color: Int = TXT2, face: Typeface = body(c), line: Float = 1.4f) = TextView(c).apply {
        text = s; textSize = size; setTextColor(color); typeface = face; setLineSpacing(0f, line)
    }
    fun h1(c: Context, s: CharSequence, size: Float = 26f) = text(c, s, size, TXT, head(c), 1.15f)
    fun label(c: Context, s: CharSequence, color: Int = MUTED) = text(c, s, 13f, color, mono(c), 1.2f).apply { letterSpacing = 0.15f }
    fun muted(c: Context, s: CharSequence) = text(c, s, 13f, MUTED)

    // ---------------- header ----------------
    enum class Left { NONE, CLOSE, BACK, CLOSE_DISABLED }

    /** The 44 dp slot on the left is always there, so the label never jumps between screens. */
    fun header(c: Context, label: String, left: Left = Left.NONE, onLeft: () -> Unit = {}, right: View? = null): View {
        val row = LinearLayout(c).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL; minimumHeight = dpi(c, 44) }
        val slot: View = when (left) {
            Left.NONE -> View(c)
            Left.CLOSE, Left.CLOSE_DISABLED -> iconButton(c, Icon.CLOSE, if (left == Left.CLOSE) "Закрыть" else "Сейчас закрыть нельзя", left == Left.CLOSE, onLeft)
            Left.BACK -> iconButton(c, Icon.BACK, "Назад", true, onLeft)
        }
        row.addView(slot, LinearLayout.LayoutParams(dpi(c, 44), dpi(c, 44)))
        row.addView(label(c, label), LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f).apply { marginStart = dpi(c, 12) })
        if (right != null) row.addView(right)
        return row
    }

    fun iconButton(c: Context, icon: Icon, desc: String, enabled: Boolean = true, onClick: () -> Unit): View = FrameLayout(c).apply {
        background = GradientDrawable().apply { cornerRadius = dp(c, 14f); setStroke(dpi(c, 2), CTRL) }
        contentDescription = desc; isEnabled = enabled; alpha = if (enabled) 1f else 0.4f
        addView(IconView(c, icon, TXT), FrameLayout.LayoutParams(dpi(c, 20), dpi(c, 20), Gravity.CENTER))
        if (enabled) setOnClickListener { onClick() }
        layoutParams = LinearLayout.LayoutParams(dpi(c, 44), dpi(c, 44))
    }

    // ---------------- buttons ----------------
    enum class Kind { PRIMARY, SECONDARY, DISABLED }

    fun button(c: Context, label: String, kind: Kind = Kind.PRIMARY, onClick: () -> Unit = {}): TextView = android.widget.Button(c).apply {
        // настоящая Button: диктор называет её кнопкой; стандартная тень и капс отключены
        text = label; isAllCaps = false; stateListAnimator = null; minWidth = 0
        gravity = Gravity.CENTER; setPadding(dpi(c, 16), dpi(c, 6), dpi(c, 16), dpi(c, 6))
        minHeight = dpi(c, if (kind == Kind.SECONDARY) 52 else 56)
        val bg = GradientDrawable().apply { cornerRadius = dp(c, 14f) }
        when (kind) {
            Kind.PRIMARY -> { bg.setColor(ACT); setTextColor(ACT_INK); typeface = head(c); textSize = 18f }
            Kind.SECONDARY -> { bg.setStroke(dpi(c, 2), CTRL); setTextColor(TXT); typeface = bodyBold(c); textSize = 16f }
            Kind.DISABLED -> { bg.setColor(DIS); setTextColor(MUTED); typeface = head(c); textSize = 18f }
        }
        background = bg
        isEnabled = kind != Kind.DISABLED
        if (kind != Kind.DISABLED) setOnClickListener { onClick() }
    }

    fun textLink(c: Context, label: String, onClick: () -> Unit) = text(c, label, 15f, TXT2).apply {
        gravity = Gravity.CENTER; minHeight = dpi(c, 44); paintFlags = paintFlags or Paint.UNDERLINE_TEXT_FLAG
        setOnClickListener { onClick() }
    }

    // ---------------- blocks ----------------
    fun banner(c: Context, ok: Boolean, s: CharSequence): View = LinearLayout(c).apply {
        orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
        setPadding(dpi(c, 14), dpi(c, 12), dpi(c, 14), dpi(c, 12))
        background = GradientDrawable().apply {
            cornerRadius = dp(c, 14f)
            setColor(Color.parseColor(if (ok) "#10301F" else "#3A1A1C")); setStroke(dpi(c, 2), if (ok) Color.parseColor("#2E9B63") else ALERT)
        }
        addView(IconView(c, if (ok) Icon.CHECK else Icon.INFO, if (ok) OK else ALERT), LinearLayout.LayoutParams(dpi(c, 20), dpi(c, 20)))
        addView(text(c, s, 15f, Color.parseColor(if (ok) "#BFFFDC" else "#FFD3CF")), LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f).apply { marginStart = dpi(c, 12) })
    }

    private fun cardBg(c: Context, pressable: Boolean) = GradientDrawable().apply {
        cornerRadius = dp(c, 14f); setColor(SURF); setStroke(dpi(c, if (pressable) 2 else 1), if (pressable) CTRL else LINE)
    }

    /** An information row (thin border) or, with onClick, a pressable one (strong border + chevron). */
    fun row(c: Context, icon: View?, title: CharSequence, sub: CharSequence? = null, right: CharSequence? = null,
            onClick: (() -> Unit)? = null): View = LinearLayout(c).apply {
        orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
        minimumHeight = dpi(c, if (onClick != null) 56 else 52)
        setPadding(dpi(c, 14), dpi(c, 8), dpi(c, 12), dpi(c, 8))
        background = cardBg(c, onClick != null)
        if (icon != null) addView(icon, ((icon.layoutParams as? LinearLayout.LayoutParams) ?: LinearLayout.LayoutParams(dpi(c, 22), dpi(c, 22)))
            .apply { marginEnd = dpi(c, 12) })
        val t = LinearLayout(c).apply { orientation = LinearLayout.VERTICAL }
        t.addView(text(c, title, 15f, TXT, if (onClick != null) bodyBold(c) else body(c), 1.25f))
        if (sub != null) t.addView(text(c, sub, 13f, MUTED, line = 1.3f))
        addView(t, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        if (right != null) addView(text(c, right, 13f, MUTED, mono(c)).apply { setPadding(dpi(c, 8), 0, 0, 0) })
        if (onClick != null) {
            addView(IconView(c, Icon.CHEVRON, TXT2), LinearLayout.LayoutParams(dpi(c, 18), dpi(c, 18)).apply { marginStart = dpi(c, 8) })
            setOnClickListener { onClick() }
        }
    }

    /** Gadget frame: 4 screws, a plate on the top edge. Returns (frame, column to fill). */
    fun gadget(c: Context, plate: String): Pair<View, LinearLayout> {
        val inner = LinearLayout(c).apply { orientation = LinearLayout.VERTICAL }
        val frame = GadgetFrame(c, plate)
        frame.addView(inner, FrameLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply {
            setMargins(dpi(c, 18), dpi(c, 24), dpi(c, 18), dpi(c, 18))
        })
        frame.layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT)
        return frame to inner
    }

    @SuppressLint("ViewConstructor")
    class GadgetFrame(c: Context, var plate: String) : FrameLayout(c) {
        private val d = c.resources.displayMetrics.density
        private val border = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 2 * d; color = CTRL }
        private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = SURF }
        private val screw = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = SCREW }
        private val slot = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = DEEP; strokeWidth = 1.2f * d }
        private val plateBg = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = DEEP }
        private val plateBorder = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = d; color = CTRL }
        private val plateText = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = MUTED; textSize = 12 * d; typeface = mono(c); textAlign = Paint.Align.CENTER }
        private val top = 12 * d   // the plate sticks out above the frame

        init { setWillNotDraw(false); setPadding(0, top.toInt(), 0, 0) }

        override fun onDraw(cv: Canvas) {
            val r = RectF(d, top, width - d, height - d)
            cv.drawRoundRect(r, 22 * d, 22 * d, fill); cv.drawRoundRect(r, 22 * d, 22 * d, border)
            for ((x, y) in listOf(r.left + 14 * d to r.top + 14 * d, r.right - 14 * d to r.top + 14 * d,
                    r.left + 14 * d to r.bottom - 14 * d, r.right - 14 * d to r.bottom - 14 * d)) {
                cv.drawCircle(x, y, 4 * d, screw); cv.drawLine(x - 2.4f * d, y + 2.4f * d, x + 2.4f * d, y - 2.4f * d, slot)
            }
            val w = plateText.measureText(plate) + 16 * d
            val pr = RectF(width / 2f - w / 2, top - 11 * d, width / 2f + w / 2, top + 11 * d)
            cv.drawRoundRect(pr, 4 * d, 4 * d, plateBg); cv.drawRoundRect(pr, 4 * d, 4 * d, plateBorder)
            cv.drawText(plate, width / 2f, top + 4.2f * d, plateText)
        }
    }

    /** A paper note with a strip of tape. Returns (note, column to fill). */
    fun paper(c: Context, title: String, tilt: Float = -0.5f): Pair<View, LinearLayout> {
        val col = LinearLayout(c).apply { orientation = LinearLayout.VERTICAL }
        val note = PaperNote(c)
        note.rotation = tilt
        note.addView(text(c, title, 13f, PAPER_SUB, mono(c)).apply { letterSpacing = 0.08f })
        note.addView(col)
        note.layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT)
        return note to col
    }

    class PaperNote(c: Context) : LinearLayout(c) {
        private val d = c.resources.displayMetrics.density
        private val tape = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = TAPE; alpha = 230 }
        init {
            orientation = VERTICAL; setWillNotDraw(false)
            setPadding((16 * d).toInt(), (16 * d).toInt(), (16 * d).toInt(), (14 * d).toInt())
            background = GradientDrawable().apply { cornerRadius = 4 * d; setColor(PAPER) }
        }
        override fun dispatchDraw(cv: Canvas) {
            super.dispatchDraw(cv)
            cv.save(); cv.rotate(-2f, width * 0.45f, 0f)
            cv.drawRect(width * 0.42f, -7 * d, width * 0.42f + 56 * d, 7 * d, tape); cv.restore()
        }
    }

    fun eventLine(c: Context, time: String, what: String): View = LinearLayout(c).apply {
        orientation = LinearLayout.HORIZONTAL; setPadding(0, dpi(c, 4), 0, 0)
        addView(text(c, time, 15f, PAPER_SUB, mono(c)))
        addView(text(c, what, 15f, PAPER_INK).apply { setPadding(dpi(c, 10), 0, 0, 0) })
    }

    fun chip(c: Context, s: String, color: Int = TXT2) = text(c, s, 13f, color, mono(c)).apply {
        setPadding(dpi(c, 10), dpi(c, 6), dpi(c, 10), dpi(c, 6)); maxLines = 1
        background = GradientDrawable().apply { cornerRadius = dp(c, 8f); setColor(DEEP); setStroke(dpi(c, 1), LINE) }
    }

    /** Progress track; the returned setter takes 0..100. */
    fun progress(c: Context): Pair<View, (Int) -> Unit> {
        val track = FrameLayout(c).apply { background = GradientDrawable().apply { cornerRadius = dp(c, 8f); setColor(DIS) } }
        val bar = View(c).apply { background = GradientDrawable().apply { cornerRadius = dp(c, 8f); setColor(OK) } }
        track.addView(bar, FrameLayout.LayoutParams(0, MATCH_PARENT))
        track.layoutParams = LinearLayout.LayoutParams(MATCH_PARENT, dpi(c, 10))
        val set: (Int) -> Unit = { p -> track.post { bar.layoutParams = bar.layoutParams.apply { width = track.width * p.coerceIn(0, 100) / 100 } } }
        return track to set
    }

    /** A stage line of a running job: 2 done ✓, 1 now ●, 0 next ○. */
    fun stage(c: Context, state: Int, s: String, right: String = ""): View = LinearLayout(c).apply {
        orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL; minimumHeight = dpi(c, 28)
        addView(when (state) {
            2 -> IconView(c, Icon.CHECK, OK)
            1 -> DotView(c, OK, true)
            else -> DotView(c, CTRL, false)
        }, LinearLayout.LayoutParams(dpi(c, 20), dpi(c, 20)))
        addView(text(c, s, 15f, when (state) { 2 -> TXT2; 1 -> TXT; else -> MUTED }), LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f).apply { marginStart = dpi(c, 12) })
        if (right.isNotEmpty()) addView(text(c, right, 13f, if (state == 1) OK else MUTED, mono(c)))
    }

    class DotView(c: Context, color: Int, filled: Boolean) : View(c) {
        private val p = Paint(Paint.ANTI_ALIAS_FLAG).apply { this.color = color; style = if (filled) Paint.Style.FILL else Paint.Style.STROKE; strokeWidth = 1.5f * c.resources.displayMetrics.density }
        override fun onDraw(cv: Canvas) { cv.drawCircle(width / 2f, height / 2f, width / 4f, p) }
    }

    /** Labelled input. Passwords get a «показать» toggle on the right. */
    fun field(c: Context, labelText: String, hint: String, password: Boolean = false, error: Boolean = false): Pair<View, EditText> {
        val box = LinearLayout(c).apply { orientation = LinearLayout.VERTICAL }
        box.addView(text(c, labelText, 14f, TXT2))
        val frame = LinearLayout(c).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL; minimumHeight = dpi(c, 52)
            background = GradientDrawable().apply { cornerRadius = dp(c, 14f); setColor(SURF); setStroke(dpi(c, 2), if (error) ALERT else CTRL) }
        }
        val e = EditText(c).apply {
            this.hint = hint; setTextColor(TXT); setHintTextColor(MUTED); typeface = body(c); textSize = 16f
            background = null; setPadding(dpi(c, 14), dpi(c, 12), dpi(c, 8), dpi(c, 12)); setSingleLine()
            if (password) inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        frame.addView(e, LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f))
        if (password) frame.addView(text(c, "показать", 13f, TXT2, mono(c)).apply {
            gravity = Gravity.CENTER; minWidth = dpi(c, 48); minHeight = dpi(c, 48); setPadding(dpi(c, 8), 0, dpi(c, 14), 0)
            setOnClickListener {
                val hidden = e.inputType and InputType.TYPE_MASK_VARIATION == InputType.TYPE_TEXT_VARIATION_PASSWORD
                e.inputType = InputType.TYPE_CLASS_TEXT or if (hidden) InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD else InputType.TYPE_TEXT_VARIATION_PASSWORD
                e.typeface = body(c); e.setSelection(e.text.length)
                text = if (hidden) "скрыть" else "показать"
            }
        })
        box.addView(frame, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT).apply { topMargin = dpi(c, 8) })
        return box to e
    }

    // ---------------- icons ----------------
    enum class Icon(val path: String, val filled: Boolean = false) {
        CLOSE("M6 6l12 12M18 6L6 18"), BACK("M15 5l-7 7 7 7"), CHEVRON("M9 5l7 7-7 7"), CHECK("M4 12l5 5L20 6"),
        INFO("M12 3a9 9 0 1 0 0.01 0zM12 11v5M12 8v0.5"), PLUG("M9 3v5M15 3v5M7 8h10v4a5 5 0 0 1-10 0zM12 17v4"),
        LOCK("M7 11h10a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2zM8 11V8a4 4 0 0 1 8 0v3"),
        BATT("M9 3h6a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM12 8l-2 4h4l-2 4"),
        WIFI("M2 9a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M12 19.5v0.1"),
        HOME("M3 11l9-7 9 7v9H3zM10 20v-5h4v5"), PLUS("M12 5v14M5 12h14"),
        GEAR("M12 9a3 3 0 1 0 0.01 0zM12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"),
        BELL("M6 16V11a6 6 0 0 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0"),
        PAW("M12 12c3 0 5 2 5 4s-2 4-5 4-5-2-5-4 2-4 5-4zM6 8a2 2 0 1 0 0.01 0zM10 4a2 2 0 1 0 0.01 0zM14 4a2 2 0 1 0 0.01 0zM18 8a2 2 0 1 0 0.01 0z", true),
    }

    class IconView(c: Context, private val icon: Icon, color: Int, private val stroke: Float = 2.2f) : View(c) {
        private val path: Path = PathParser.createPathFromPathData(icon.path)
        private val p = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            this.color = color; style = if (icon.filled) Paint.Style.FILL else Paint.Style.STROKE
            strokeCap = Paint.Cap.ROUND; strokeJoin = Paint.Join.ROUND
        }
        override fun onDraw(cv: Canvas) {
            val s = minOf(width, height) / 24f
            p.strokeWidth = stroke   // in the 24-unit icon space
            cv.save(); cv.translate((width - 24 * s) / 2, (height - 24 * s) / 2); cv.scale(s, s); cv.drawPath(path, p); cv.restore()
        }
    }

    // ---------------- mascot ----------------
    enum class Mood { LISTEN, DETECT, OFF }

    /** The original mascot: round body, aperture eye, legs, antenna LED (green listening, yellow somebody, grey offline). */
    class MascotView(c: Context, mood: Mood) : View(c) {
        var mood = mood; set(v) { if (field != v) { field = v; invalidate() } }
        private val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeCap = Paint.Cap.ROUND; strokeJoin = Paint.Join.ROUND }
        private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
        private val hex = PathParser.createPathFromPathData("M24 25.5l3.9 2.25v4.5L24 34.5l-3.9-2.25v-4.5z")
        private val lid = PathParser.createPathFromPathData("M16 30a8 8 0 0 1 16 0z")
        private val bent = Path().apply { moveTo(24f, 15f); quadTo(24f, 9f, 29f, 6f) }
        init { contentDescription = "Жучок" }

        override fun onDraw(cv: Canvas) {
            val s = minOf(width, height) / 48f
            cv.save(); cv.translate((width - 48 * s) / 2, (height - 48 * s) / 2); cv.scale(s, s)
            stroke.color = CTRL; stroke.strokeWidth = 2.5f
            cv.drawLine(12f, 32f, 5f, 35f, stroke); cv.drawLine(36f, 32f, 43f, 35f, stroke)
            cv.drawLine(14f, 39f, 9f, 45f, stroke); cv.drawLine(34f, 39f, 39f, 45f, stroke)
            val led = when (mood) { Mood.LISTEN -> OK; Mood.DETECT -> PRES; Mood.OFF -> CTRL }
            stroke.color = MUTED; stroke.strokeWidth = 2f; fill.color = led
            if (mood == Mood.DETECT) { cv.drawPath(bent, stroke); cv.drawCircle(29.5f, 5f, 3f, fill) }
            else { cv.drawLine(24f, 15f, 24f, 6f, stroke); cv.drawCircle(24f, 5f, 3f, fill) }
            fill.color = SURF; cv.drawCircle(24f, 29f, 14f, fill)
            stroke.color = CTRL; stroke.strokeWidth = 2f; cv.drawCircle(24f, 29f, 14f, stroke)
            stroke.color = LINE; stroke.strokeWidth = 1.5f; cv.drawLine(11f, 26f, 37f, 26f, stroke)
            fill.color = SCREW; cv.drawCircle(14.5f, 22f, 1.3f, fill); cv.drawCircle(33.5f, 22f, 1.3f, fill)
            fill.color = DEEP; cv.drawCircle(24f, 30f, 8f, fill)
            stroke.color = TXT; stroke.strokeWidth = 1.5f; cv.drawCircle(24f, 30f, 8f, stroke)
            val pupil = if (mood == Mood.OFF) CTRL else OK
            stroke.color = pupil; stroke.strokeWidth = 1.4f; cv.drawPath(hex, stroke)
            fill.color = pupil; cv.drawCircle(24f, 30f, 1.8f, fill)
            if (mood == Mood.OFF) { fill.color = SURF; cv.drawPath(lid, fill); stroke.color = TXT; stroke.strokeWidth = 1.5f; cv.drawPath(lid, stroke) }
            cv.restore()
        }
    }

    fun <T : View> sized(c: Context, v: T, w: Int, h: Int = w): T = v.apply { layoutParams = LinearLayout.LayoutParams(dpi(c, w), dpi(c, h)) }

    fun hrow(c: Context, vararg views: View, gapDp: Int = 12): LinearLayout = LinearLayout(c).apply {
        orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
        views.forEachIndexed { i, v ->
            val lp = (v.layoutParams as? LinearLayout.LayoutParams) ?: LinearLayout.LayoutParams(WRAP_CONTENT, WRAP_CONTENT)
            if (i > 0) lp.marginStart = dpi(c, gapDp)
            addView(v, lp)
        }
    }

    fun column(c: Context): LinearLayout = LinearLayout(c).apply { orientation = LinearLayout.VERTICAL }
}
