package com.mauntingstudios.smart_system

import android.app.Activity
import app.tauri.annotation.Command
import app.tauri.annotation.TauriPlugin
import app.tauri.plugin.Invoke
import app.tauri.plugin.Plugin

/**
 * Nimmt den Erinnerungsplan der offenen App entgegen (`erinnerung.rs`, dort
 * schon geprüft) und gibt ihn an Android (`Erinnerungen.planen`).
 */
@TauriPlugin
class ErinnerungPlugin(private val activity: Activity) : Plugin(activity) {

    @Command
    fun planen(invoke: Invoke) {
        try {
            val liste = invoke.getArgs().getJSONArray("erinnerungen")
            val neu = (0 until liste.length()).map { i ->
                val o = liste.getJSONObject(i)
                Erinnerungen.Erinnerung(o.getString("schluessel"), o.getLong("zeit"), o.getString("titel"), o.getString("text"), o.getString("oeffentlich"))
            }
            Erinnerungen.planen(activity.applicationContext, neu)
            invoke.resolve()
        } catch (e: Exception) {
            invoke.reject(e.message ?: "Erinnerungen konnten nicht geplant werden")
        }
    }
}
