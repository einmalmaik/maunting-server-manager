package com.mauntingstudios.smart_system

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * Nach einem Neustart des Geräts oder einem Update der App hat Android alle
 * Wecker vergessen. Die geplanten Terminerinnerungen liegen noch in der Ablage
 * und werden hier neu gestellt (`Erinnerungen.neuStellen`).
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action
        if (
            Intent.ACTION_BOOT_COMPLETED == action ||
            "android.intent.action.QUICKBOOT_POWERON" == action ||
            Intent.ACTION_MY_PACKAGE_REPLACED == action
        ) {
            Erinnerungen.neuStellen(context)
        }
    }
}
