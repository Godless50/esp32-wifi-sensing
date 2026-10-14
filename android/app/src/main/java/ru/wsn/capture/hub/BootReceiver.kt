package ru.wsn.capture.hub

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import java.io.File

/** The phone is the hub: after a reboot it starts listening to the boards again (if setup was done). */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return
        if (File(context.filesDir, "device_keys.json").exists()) HubService.start(context)
    }
}
