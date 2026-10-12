package ru.wsn.capture

/**
 * Chooses the ARCore camera config. On Honor 90 the camera HAL rejects the 1920x1080 CPU-image stream that
 * Chrome's WebXR asks for, so we take the smallest CPU image on the back camera, preferring 30 FPS.
 */
object CameraConfigPicker {
    data class Option(val index: Int, val cpuWidth: Int, val cpuHeight: Int, val fpsMax: Int, val back: Boolean)

    fun pick(options: List<Option>): Option? {
        val back = options.filter { it.back }
        if (back.isEmpty()) return null
        val at30 = back.filter { it.fpsMax == 30 }
        val pool = at30.ifEmpty { back }
        return pool.minWithOrNull(compareBy<Option> { it.cpuWidth * it.cpuHeight }.thenBy { it.index })
    }
}
