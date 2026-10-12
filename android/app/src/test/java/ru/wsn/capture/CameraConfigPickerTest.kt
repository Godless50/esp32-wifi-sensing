package ru.wsn.capture

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class CameraConfigPickerTest {
    private fun opt(i: Int, w: Int, h: Int, fpsMax: Int, back: Boolean = true) =
        CameraConfigPicker.Option(i, w, h, fpsMax, back)

    @Test
    fun picksSmallestCpuImageAt30fps() {
        val list = listOf(opt(0, 1920, 1080, 30), opt(1, 640, 480, 30), opt(2, 1280, 720, 30), opt(3, 640, 480, 60))
        assertEquals(1, CameraConfigPicker.pick(list)?.index)
    }

    @Test
    fun ignoresFrontCamera() {
        val list = listOf(opt(0, 320, 240, 30, back = false), opt(1, 1280, 720, 30))
        assertEquals(1, CameraConfigPicker.pick(list)?.index)
    }

    @Test
    fun fallsBackToSmallestWhenNo30fps() {
        val list = listOf(opt(0, 1920, 1080, 60), opt(1, 1280, 720, 60))
        assertEquals(1, CameraConfigPicker.pick(list)?.index)
    }

    @Test
    fun emptyGivesNull() {
        assertNull(CameraConfigPicker.pick(emptyList()))
    }
}
