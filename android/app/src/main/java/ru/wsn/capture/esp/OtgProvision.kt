package ru.wsn.capture.esp

/** The whole "connect the board by cable" step of the wizard, platform-free so it can be unit-tested: flash the
 *  right merged image over the ROM loader, let the board boot the app firmware, then provision it over the REPL.
 *  The two phases use the same SerialPort; `reopen` makes a fresh one (the ROM and the app enumerate as new USB
 *  devices on the phone). The caller supplies the images (read from app assets) and the per-board key. */
class OtgProvision(
    private val openPort: () -> SerialPort,
    private val images: Map<String, ByteArray>,   // "main"/"lite" -> merged image bytes (flashed at 0x0)
    private val log: (String) -> Unit = {},
) {
    data class Settings(val ssid: String, val password: String, val bssid: String, val host: String, val port: Int)
    data class Result(val mac: String, val image: String, val flashMd5Ok: Boolean)

    /** Returns the board MAC and which image was flashed. onProgress: 0..100 during the write. */
    /** beforeProvision(mac): called once the board's MAC is known and before the key goes into it — the caller
     *  stores the key first, so a failure later never leaves a board with a key the phone does not have. */
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

        Thread.sleep(2500)                               // the app firmware boots and re-enumerates
        port = openPort()
        val console = Console(port)
        console.status() ?: throw EspLoader.EspError("плата не отвечает после прошивки")
        val bssid = s.bssid.ifEmpty {  // узел сети, который плата слышит лучше всего (в mesh у каждой платы свой)
            log("ищу сеть «${s.ssid}»")
            console.bestBssid(s.ssid) ?: throw EspLoader.EspError("плата не видит сеть «${s.ssid}» (2,4 ГГц?)")
        }
        beforeProvision(info.mac)
        val mac = console.provision(Console.Provision(keyHex, s.ssid, s.password, bssid, s.host, s.port))
        port.close()
        log("provisioned $mac on image $which")
        return Result(mac, which, true)
    }
}
