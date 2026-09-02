package com.mauntingstudios.smart_system

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import org.json.JSONObject
import java.io.File

/**
 * Autostart-Receiver: Startet den Hintergrunddienst nach dem Hochfahren
 * des Mobilgeräts (BOOT_COMPLETED) oder nach einem App-Update (MY_PACKAGE_REPLACED),
 * sofern der Autostart in den Einstellungen nicht deaktiviert wurde.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action
        if (
            Intent.ACTION_BOOT_COMPLETED == action ||
            "android.intent.action.QUICKBOOT_POWERON" == action ||
            Intent.ACTION_MY_PACKAGE_REPLACED == action
        ) {
            try {
                if (isAutostartEnabled(context)) {
                    MsmBackgroundAlertService.start(context)
                }
            } catch (e: Exception) {
                e.printStackTrace()
            }
        }
    }

    private fun isAutostartEnabled(context: Context): Boolean {
        try {
            val file = File(context.filesDir, "konfig.json")
            if (file.exists()) {
                val json = JSONObject(file.readText())
                if (json.has("autostart_aktiv")) {
                    return json.getBoolean("autostart_aktiv")
                }
            }
        } catch (e: Exception) {
            e.printStackTrace()
        }
        return true
    }
}
