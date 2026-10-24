package com.mauntingstudios.secure_browser

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import app.tauri.annotation.Command
import app.tauri.annotation.InvokeArg
import app.tauri.annotation.TauriPlugin
import app.tauri.plugin.Invoke
import app.tauri.plugin.JSObject
import app.tauri.plugin.Plugin

/**
 * Steuerung des Dauerdienstes von der Oberfläche aus.
 *
 * Die Oberfläche ruft `dienst_starten`, `dienst_stoppen`, `dienst_mailboxen`,
 * `dienst_stand`, `dienst_akku_befreien`; `dienst.rs` reicht sie hier als
 * `starten`, `stoppen`, `mailboxen`, `laeuft`, `akkuBefreien` durch. Geprüft
 * wird hier nur die Form — den Ausweis prüft der Server, und ob der Mensch ihn
 * anlegen durfte, hat er vorher mit einem frischen Nachweis belegt.
 */
@InvokeArg
class DienstStartArgs {
    var ausweis: String = ""
    var basis: String = ""
    var mailboxen: String = "[]"
}

@InvokeArg
class DienstMailboxArgs {
    var mailboxen: String = "[]"
}

@TauriPlugin
class DienstPlugin(private val activity: Activity) : Plugin(activity) {

    @Command
    fun starten(invoke: Invoke) {
        val args = try {
            invoke.parseArgs(DienstStartArgs::class.java)
        } catch (e: Exception) {
            invoke.reject("Unvollständige Anfrage")
            return
        }
        // Ein Ausweis ist 64 Hexzeichen; alles andere wäre ein Tippfehler und
        // würde still einen Dienst starten, der nie etwas hört.
        if (args.ausweis.length != 64 || !args.basis.startsWith("http")) {
            invoke.reject("Unvollständiger Kanal-Ausweis")
            return
        }
        if (!args.mailboxen.trimStart().startsWith("[")) {
            invoke.reject("Mailboxliste ist kein JSON")
            return
        }

        val ctx = activity.applicationContext
        Hintergrunddienst.einrichten(ctx, args.ausweis, args.basis, args.mailboxen)
        Hintergrunddienst.starten(ctx)

        // Ab Android 13 darf ohne diese Erlaubnis keine Meldung erscheinen —
        // der Dienst liefe, aber niemand sähe etwas.
        if (Build.VERSION.SDK_INT >= 33 &&
            activity.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            activity.requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 4201)
        }
        invoke.resolve()
    }

    @Command
    fun stoppen(invoke: Invoke) {
        Hintergrunddienst.stoppen(activity.applicationContext)
        invoke.resolve()
    }

    @Command
    fun mailboxen(invoke: Invoke) {
        val args = try {
            invoke.parseArgs(DienstMailboxArgs::class.java)
        } catch (e: Exception) {
            invoke.reject("Unvollständige Anfrage")
            return
        }
        if (!args.mailboxen.trimStart().startsWith("[")) {
            invoke.reject("Mailboxliste ist kein JSON")
            return
        }
        // Der Dienst läuft in einem eigenen Prozess; ein Merker hier erreichte
        // ihn nicht. `mailboxenSetzen` schickt die Liste mit einem Start-Intent
        // hinüber und merkt sie für den nächsten Start.
        Hintergrunddienst.mailboxenSetzen(activity.applicationContext, args.mailboxen)
        invoke.resolve()
    }

    @Command
    fun laeuft(invoke: Invoke) {
        val ctx = activity.applicationContext
        val energie = activity.getSystemService(Context.POWER_SERVICE) as PowerManager
        invoke.resolve(
            JSObject().apply {
                put("aktiv", Hintergrunddienst.istEingeschaltet(ctx))
                put("akkuBefreit", energie.isIgnoringBatteryOptimizations(activity.packageName))
            },
        )
    }

    /**
     * Fragt die Akku-Ausnahme an.
     *
     * Ohne sie beenden Samsung und Xiaomi den Dienst nach Stunden still — und
     * genau das ist der Unterschied zwischen „kommt an" und „kommt manchmal
     * an". Der Dialog gehört dem System; wir öffnen ihn nur.
     */
    @Command
    fun akkuBefreien(invoke: Invoke) {
        try {
            val energie = activity.getSystemService(Context.POWER_SERVICE) as PowerManager
            if (!energie.isIgnoringBatteryOptimizations(activity.packageName)) {
                activity.startActivity(
                    Intent(
                        Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                        Uri.parse("package:${activity.packageName}"),
                    ),
                )
            }
            invoke.resolve()
        } catch (e: Exception) {
            invoke.reject(e.message ?: "Akku-Ausnahme nicht erreichbar")
        }
    }
}
