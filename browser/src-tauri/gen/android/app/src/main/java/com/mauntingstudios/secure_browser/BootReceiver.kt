package com.mauntingstudios.secure_browser

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * Nach einem Neustart oder einem Update: den Dauerdienst wieder aufstellen.
 *
 * Nur wenn der Mensch ihn eingeschaltet hat (`Hintergrunddienst.nachNeustart`).
 * Ein Neustart darf nichts anmachen, was jemand ausgeschaltet hat.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action
        if (
            Intent.ACTION_BOOT_COMPLETED == action ||
            "android.intent.action.QUICKBOOT_POWERON" == action ||
            Intent.ACTION_MY_PACKAGE_REPLACED == action
        ) {
            Hintergrunddienst.nachNeustart(context)
        }
    }
}
