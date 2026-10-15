package ru.wsn.capture

import android.app.Activity
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Canvas
import android.os.Looper
import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.File

/** Screenshots of every setup page and every hub state, drawn by the real code (no phone needed), into
 *  build/screens/NAME.png — for the design review. Fails only if a screen cannot be built at all. */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [34], qualifiers = "w390dp-h844dp-xhdpi")
class ScreensTest {
    private val out = File("build/screens").apply { mkdirs() }

    private fun snap(a: Activity, name: String) {
        shadowOf(Looper.getMainLooper()).idle()
        val v = a.window.decorView
        val bmp = Bitmap.createBitmap(v.width, v.height, Bitmap.Config.ARGB_8888)
        v.draw(Canvas(bmp))
        File(out, "$name.png").outputStream().use { bmp.compress(Bitmap.CompressFormat.PNG, 100, it) }
        assertTrue(File(out, "$name.png").length() > 0)
    }

    @Test fun setupPages() {
        for (p in listOf("INTRO", "BRIEF", "CABLE", "PLACE", "MORE", "SURVEY", "WALK", "TRAIN", "DONE")) {
            val a = Robolectric.buildActivity(SetupActivity::class.java, Intent().putExtra(SetupActivity.EXTRA_PAGE, p)).setup().get()
            snap(a, "setup_${p.lowercase()}")
        }
    }

    private val rooms = JSONObject("""{"rooms_ok": true,
        "rooms": [{"name": "Кухня", "polygon": [[0,0],[3,0],[3,3],[0,3]]}, {"name": "Зал", "polygon": [[3,0],[8,0],[8,3],[3,3]]},
                  {"name": "Спальня", "polygon": [[0,3],[4,3],[4,6],[0,6]]}, {"name": "Коридор", "polygon": [[4,3],[8,3],[8,6],[4,6]]}],
        "boards": {"7c:4f:ad:bc:9b:54": [2.6, 0.4], "7c:4f:ad:bc:9b:55": [0.4, 5.6]}}""")

    @Test fun hubStates() {
        val cases = listOf(
            "hub_present_one_zone" to listOf("PRESENT", true, null, false, 1, 1, null),
            "hub_empty_one_zone" to listOf("EMPTY", false, null, false, 1, 1, null),
            "hub_present_rooms" to listOf("PRESENT", true, "Кухня", true, 2, 2, rooms),
            "hub_offline" to listOf("OFFLINE", false, null, false, 0, 1, null),
            "hub_not_set_up" to listOf("NO_MODEL", false, null, false, 0, 0, null),
        )
        for ((name, c) in cases) {
            val a = Robolectric.buildActivity(HubMapActivity::class.java).setup().get()
            a.preview(c[0] as String, c[1] as Boolean, c[2] as String?, c[3] as Boolean, c[4] as Int, c[5] as Int, c[6] as JSONObject?)
            snap(a, name)
        }
    }
}
