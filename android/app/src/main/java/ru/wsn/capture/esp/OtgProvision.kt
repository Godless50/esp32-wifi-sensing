package ru.wsn.capture.esp

/** The whole "connect the board by cable" step of the wizard, platform-free so it can be unit-tested: flash the
 *  right merged image over the ROM loader, let the board boot the app firmware, provision it over the REPL, then
 *  watch it join the home Wi-Fi while the cable is still in — a wrong password is caught here, not after the
 *  board is on the shelf. The phases use the same SerialPort factory; `reopen` makes a fresh one (the ROM and the
 *  app enumerate as new USB devices on the phone, and so does every reboot). The caller supplies the images (read
 *  from app assets) and the per-board key. */
class OtgProvision(
    private val openPort: () -> SerialPort,
    private val images: Map<String, ByteArray>,   // "main"/"lite" -> merged image bytes (flashed at 0x0)
    private val sleep: (Long) -> Unit = { Thread.sleep(it) },
    private val wifiTimeoutMs: Long = 40_000,
    private val log: (String) -> Unit = {},       // last: callers pass it as a trailing lambda
) {
    data class Settings(val ssid: String, val password: String, val bssid: String, val host: String, val port: Int)
    data class Result(val mac: String, val image: String, val flashMd5Ok: Boolean, val ip: String)

    /** The board is flashed and keyed but did not join the network. The cable is still in: ask for the password
     *  again and call [retryWifi]; nothing has to be flashed twice. */
    class WifiFailed(val outcome: Console.Wifi, val mac: String) : Exception(when (outcome) {
        Console.Wifi.AUTH_FAIL -> "роутер не пустил плату: пароль Wi-Fi не подошёл"
        Console.Wifi.NOT_FOUND -> "плата не находит эту сеть (нужна сеть 2,4 ГГц рядом с платой)"
        else -> "плата не подключилась к Wi-Fi (роутер далеко или выключен?)"
    })

    /** Returns the board MAC, which image was flashed and the address the board got.
     *  onProgress: 0..100 during the write. beforeProvision(mac): called once the board's MAC is known and before
     *  the key goes into it — the caller stores the key first, so a failure later never leaves a board with a key
     *  the phone does not have. Throws [WifiFailed] when everything but the network worked. */
    fun run(keyHex: String, s: Settings, onProgress: (Int) -> Unit = {}, beforeProvision: (String) -> Unit = {}): Result {
        var port = openPort()
        val loader = EspLoader(port)
        loader.connect()
        loader.disableWatchdogs()                        // иначе сторож приложения сбрасывает чип посреди записи
        loader.spiAttach()
        val chip = loader.chipInfo()
        val info = chip.copy(flashMb = loader.flashSizeMb(chip))
        log("chip ${info.chipId} mac ${info.mac} flash ${info.flashMb}MB psram=${info.psramOctal}")
        val which = loader.chooseImage(info)
        val image = images[which] ?: throw EspLoader.EspError("no image '$which' bundled")
        loader.spiSetParams(info.flashMb.toLong() * 1024 * 1024)
        loader.flashImage(0x0, image, onProgress)       // verifies by MD5, throws on mismatch
        loader.watchdogReset()                           // запуск прошивки (выход из ПЗУ по USB-Serial/JTAG)
        port.close()

        sleep(2500)                                      // the app firmware boots and re-enumerates
        port = openPort()
        val console = Console(port)
        console.status() ?: throw EspLoader.EspError("плата не отвечает после прошивки")
        val bssid = bssidFor(console, s)
        beforeProvision(info.mac)
        val mac = console.provision(Console.Provision(keyHex, s.ssid, s.password, bssid, s.host, s.port))
        port.close()
        log("Плата настроена. Проверяем, подключится ли она к Wi-Fi…")
        val ip = awaitWifi(mac)
        return Result(mac, which, true, ip)
    }

    /** After [WifiFailed]: the same board (still on the cable) gets another network/password and is watched again.
     *  Key and server stay in the board. Returns the address it got; throws [WifiFailed] again if it still fails. */
    fun retryWifi(s: Settings): Result {
        val port = openPort()
        val console = Console(port)
        val st = console.status() ?: run { port.close(); throw EspLoader.EspError("плата не отвечает по кабелю") }
        val mac = st["mac"] ?: run { port.close(); throw EspLoader.EspError("no MAC in STATUS") }
        try {
            console.setWifi(s.ssid, s.password, bssidFor(console, s))
            console.send("reboot")
        } finally { port.close() }
        log("Проверяем, подключится ли плата к Wi-Fi…")
        return Result(mac, "", true, awaitWifi(mac))
    }

    private fun bssidFor(console: Console, s: Settings) = s.bssid.ifEmpty {
        // узел сети, который плата слышит лучше всего (в mesh у каждой платы свой)
        log("ищу сеть «${s.ssid}»")
        console.bestBssid(s.ssid) ?: throw EspLoader.EspError("плата не видит сеть «${s.ssid}» (2,4 ГГц?)")
    }

    /** The board reboots after the settings and re-enumerates; watch its STATUS until it is on the network. */
    private fun awaitWifi(mac: String): String {
        sleep(2500)
        val port = openPort()
        try {
            val (outcome, ip) = Console(port).waitWifi(wifiTimeoutMs)
            if (outcome != Console.Wifi.CONNECTED || ip == null) throw WifiFailed(outcome, mac)
            log("Плата подключилась к Wi-Fi")
            return ip
        } finally { port.close() }
    }
}
