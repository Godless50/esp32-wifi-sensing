package ru.wsn.capture

import android.content.Intent
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Home screen: pairing with the server, what works, and the two things to do — mark & record, and the map. */
class HomeActivity : AppCompatActivity() {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var info: TextView
    private lateinit var bPair: Button
    private lateinit var bCapture: Button
    private lateinit var bMap: Button
    private var polling = false

    private val scan = registerForActivityResult(ScanContract()) { r ->
        val text = r.contents ?: return@registerForActivityResult
        val p = PairInfo.parse(text)
        if (p == null) toastDialog("Это не QR-код сопряжения. Покажите код из ./install.sh pair") else askPassword(p)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Server.init(this)
        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(40, 72, 40, 40) }
        col.addView(TextView(this).apply { text = "WSN — присутствие по Wi-Fi"; textSize = 24f; setTextColor(Color.WHITE) })
        info = TextView(this).apply { textSize = 15f; setTextColor(Color.rgb(209, 213, 219)); setPadding(0, 32, 0, 32) }
        col.addView(info)
        bPair = button("Сопряжение с сервером (QR)", false) { startScan() }
        bPair.setOnLongClickListener { manualPair(); true }
        bCapture = button("Разметка квартиры и запись сценария", true) { startActivity(Intent(this, CaptureActivity::class.java)) }
        bMap = button("Карта", true) { startActivity(Intent(this, MapActivity::class.java)) }
        val bPw = button("Пароль сервера", false) { askPassword(null) }
        for (b in listOf(bCapture, bMap, bPair, bPw)) col.addView(b, LinearLayout.LayoutParams(MATCH_PARENT, 150).apply { setMargins(0, 12, 0, 12) })
        col.addView(TextView(this).apply {
            textSize = 13f; setTextColor(Color.GRAY); setPadding(0, 24, 0, 0)
            text = "Порядок: 1) на компьютере ./install.sh и QR-код сопряжения; 2) разметка и сценарий здесь; " +
                "3) на компьютере ./train.sh; 4) «Карта». Долгое нажатие на «Сопряжение» — ввести адрес и код вручную."
        })
        setContentView(ScrollView(this).apply { setBackgroundColor(Color.rgb(11, 13, 17)); addView(col) })
        render(null, null)
    }

    override fun onResume() {
        super.onResume()
        polling = true
        poll()
    }

    override fun onPause() {
        super.onPause()
        polling = false
        main.removeCallbacksAndMessages(null)
    }

    private fun poll() {
        if (!polling) return
        if (Server.paired()) Server.getJson("/api/status") { js, err -> render(js, err) } else render(null, null)
        main.postDelayed({ poll() }, 3000)
    }

    private fun button(label: String, primary: Boolean, onClick: () -> Unit) = Button(this).apply {
        text = label; isAllCaps = false; textSize = 16f; setTextColor(Color.WHITE)
        background = GradientDrawable().apply { cornerRadius = 28f; setColor(if (primary) Color.rgb(59, 130, 246) else Color.argb(60, 255, 255, 255)) }
        setOnClickListener { onClick() }
    }

    private fun render(st: JSONObject?, err: String?) {
        val paired = Server.paired()
        bCapture.isEnabled = paired; bMap.isEnabled = paired
        bCapture.alpha = if (paired) 1f else 0.4f; bMap.alpha = bCapture.alpha
        if (!paired) {
            info.text = "Не сопряжено. На компьютере запустите ./install.sh (или ./install.sh pair) и отсканируйте QR-код."
            return
        }
        if (st == null) {
            info.text = "Сервер ${Server.address()}: ${err ?: "проверяю…"}" +
                if (err?.contains("401") == true) "\nНеверный пароль — «Пароль сервера»." else ""
            return
        }
        val sb = StringBuilder("Сервер ${Server.address()}: на связи\n")
        val boards = st.optJSONArray("boards")
        sb.append("Платы: ${boards?.length() ?: 0}")
        for (i in 0 until (boards?.length() ?: 0)) {
            val b = boards!!.getJSONObject(i)
            sb.append("\n  • ${b.optString("name")} — ${b.optDouble("csi_hz").toInt()} кадров/с")
        }
        val plan = st.optJSONObject("plan")
        val rooms = plan?.optJSONArray("rooms")
        sb.append("\nПлан: ").append(if (rooms != null && rooms.length() > 0)
            (0 until rooms.length()).joinToString(", ") { rooms.getString(it) } else "нет — сделайте разметку")
        val walks = st.optJSONArray("walks")
        if (walks != null && walks.length() > 0) {
            val w = walks.getJSONObject(walks.length() - 1)
            val day = SimpleDateFormat("d MMM HH:mm", Locale("ru")).format(Date((w.optDouble("started_wall") * 1000).toLong()))
            sb.append("\nЗапись обхода: $day, ${(w.optDouble("duration_s") / 60).toInt()} мин")
        }
        st.optJSONObject("recording")?.let { sb.append("\n● Идёт запись (${it.optString("kind")})") }
        val m = st.optJSONObject("model")
        val t = st.optJSONObject("training")
        sb.append("\nМодель квартиры: ").append(when {
            t?.optString("state") == "running" -> "обучается: ${t.optString("message")} (${(t.optDouble("progress") * 100).toInt()} %)"
            m?.optBoolean("loaded") == true -> {
                val acc = m.optJSONObject("metrics")?.optJSONObject("heldout")?.optDouble("zone_bal_acc", Double.NaN)
                "v${m.optLong("version")} от ${m.optString("created")}" +
                    if (acc != null && !acc.isNaN()) ", комната угадывается в ${(acc * 100).toInt()} %" else ""
            }
            t?.optString("state") == "error" -> "ошибка обучения: ${t.optString("message")}"
            else -> "нет — после записи запустите на компьютере ./train.sh"
        })
        info.text = sb.toString()
    }

    private fun startScan() {
        scan.launch(ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE).setBeepEnabled(false)
            .setOrientationLocked(false).setPrompt("QR-код из ./install.sh pair"))
    }

    private fun askPassword(p: PairInfo?) {
        val e = EditText(this).apply {
            hint = "пароль, заданный в install.sh"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        AlertDialog.Builder(this).setTitle(if (p != null) "Сервер ${p.host}" else "Пароль сервера").setView(e)
            .setPositiveButton("OK") { _, _ ->
                val pw = e.text.toString()
                if (p == null) { Server.setPassword(pw); poll(); return@setPositiveButton }
                info.text = "Сопряжение с ${p.host}…"
                Server.pair(p, pw) { error ->
                    if (error == null) { toastDialog("Сопряжено с ${p.host}:${p.port}"); poll() } else toastDialog(error)
                }
            }.setNegativeButton("Отмена", null).show()
    }

    private fun manualPair() {
        val box = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(48, 24, 48, 0) }
        val host = EditText(this).apply { hint = "адрес компьютера, напр. 192.168.1.10" }
        val code = EditText(this).apply { hint = "код сертификата, напр. ABCD-1234-5678-9EF0" }
        box.addView(host); box.addView(code)
        AlertDialog.Builder(this).setTitle("Сопряжение вручную").setView(box)
            .setPositiveButton("Далее") { _, _ ->
                val h = host.text.toString().trim()
                val (addr, port) = if (h.contains(":")) h.substringBefore(":") to h.substringAfter(":") else h to "8443"
                val p = PairInfo.fromParts(addr, port, code.text.toString())
                if (p == null) toastDialog("Нужны адрес и код из ./install.sh pair (16 знаков или больше)") else askPassword(p)
            }.setNegativeButton("Отмена", null).show()
    }

    private fun toastDialog(msg: String) {
        AlertDialog.Builder(this).setMessage(msg).setPositiveButton("OK", null).show()
    }
}
