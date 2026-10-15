package ru.wsn.capture

import android.annotation.SuppressLint
import android.graphics.Color
import android.os.Handler
import android.os.Looper
import android.view.View
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.webkit.WebViewAssetLoader
import androidx.webkit.WebViewClientCompat
import org.json.JSONObject

/** Прозрачная страница обхода (assets/ui/walk.html) поверх камеры: тот же генератор, что и в UiActivity.
 *  push(state) — полное состояние (перерисовка только при изменении), действия — onAction(action, args). */
@SuppressLint("SetJavaScriptEnabled")
class WalkHud(ctx: android.content.Context, private val onAction: (String, JSONObject) -> Unit) {
    private val main = Handler(Looper.getMainLooper())
    private var ready = false
    private var last: String? = null
    private var topPx = 0
    private val web: WebView
    /** Контейнер страницы: отступы панелей и клавиатуры — на нём (у самого WebView padding работает плохо). */
    val view: android.widget.FrameLayout

    init {
        val loader = WebViewAssetLoader.Builder().addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(ctx)).build()
        web = WebView(ctx).apply {
            setBackgroundColor(Color.TRANSPARENT)
            if (android.os.Build.VERSION.SDK_INT >= 26) importantForAutofill = View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS
            settings.javaScriptEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            settings.textZoom = 100
            overScrollMode = View.OVER_SCROLL_NEVER
            webViewClient = object : WebViewClientCompat() {
                override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
                    loader.shouldInterceptRequest(request.url)
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean = true
                override fun onPageFinished(view: WebView, url: String) {
                    ready = true
                    applyTop()
                    last?.let { send(it) }
                }
            }
            addJavascriptInterface(object {
                @JavascriptInterface fun send(action: String, json: String) {
                    val args = try { JSONObject(json) } catch (e: Exception) { JSONObject() }
                    main.post { onAction(action, args) }
                }
            }, "Android")
            loadUrl("https://appassets.androidplatform.net/assets/ui/walk.html")
        }
        view = android.widget.FrameLayout(ctx).apply { addView(web, android.widget.FrameLayout.LayoutParams(-1, -1)) }
        // Android 15 рисует под системными панелями: сверху — отступ страницы, снизу — панель и клавиатура
        ViewCompat.setOnApplyWindowInsetsListener(view) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            val ime = insets.getInsets(WindowInsetsCompat.Type.ime())
            topPx = bars.top
            v.setPadding(bars.left, 0, bars.right, maxOf(bars.bottom, ime.bottom))
            applyTop()
            WindowInsetsCompat.CONSUMED
        }
    }

    fun push(state: JSONObject) {
        val json = state.toString().replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
        if (json == last && ready) return
        last = json
        if (ready) send(json)
    }

    private fun send(json: String) = web.evaluateJavascript("window.app && window.app.setState($json)", null)

    private fun applyTop() {
        if (!ready) return
        val css = (topPx / web.resources.displayMetrics.density).toInt() + 16
        web.evaluateJavascript("document.documentElement.style.setProperty('--top','${css}px')", null)
    }

    fun destroy() { ready = false; main.removeCallbacksAndMessages(null); web.destroy() }
}
