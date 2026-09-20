package com.mauntingstudios.smart_system

import android.app.Activity
import app.tauri.annotation.Command
import app.tauri.annotation.InvokeArg
import app.tauri.annotation.TauriPlugin
import app.tauri.plugin.Invoke
import app.tauri.plugin.JSObject
import app.tauri.plugin.Plugin

@InvokeArg
class FachArgs {
    lateinit var fach: String
    var geheimnis: String? = null
    var nachricht: String? = null
}

/**
 * Die Brücke von Rust zum geschützten Schlüsselspeicher dieses Geräts.
 *
 * Das Gegenstück zum `keyring`-Kasten auf dem Desktop. Beide Seiten hängen an
 * derselben Rust-Schnittstelle (`biometrie.rs`), damit Tresor und Messenger
 * oben nicht wissen müssen, auf welchem Betriebssystem sie gerade laufen.
 *
 * Die Aufteilung: was ohne Bestätigung geht, wird hier sofort erledigt; was eine
 * braucht, geht über [SchluesselfachActivity], weil `BiometricPrompt` eine
 * `FragmentActivity` verlangt. Welches Fach welche Sorte ist, entscheidet
 * [Schluesselfach], nicht der Aufrufer.
 *
 * Alle vier Befehle halten den Aufrufer drüben in Rust auf, bis eine Antwort da
 * ist. Deshalb müssen die zugehörigen Tauri-Kommandos `async` sein — ein
 * blockierter Hauptthread heißt auf Android: der Prompt kann gar nicht erst
 * gezeichnet werden, und die App steht.
 */
@TauriPlugin
class SchluesselfachPlugin(private val activity: Activity) : Plugin(activity) {

    /** Lässt sich hier überhaupt bestätigen? Sonst gibt es keinen Schnelleinstieg. */
    @Command
    fun moeglich(invoke: Invoke) {
        val antwort = JSObject()
        antwort.put("wert", SchluesselfachActivity.bestaetigungMoeglich(activity))
        invoke.resolve(antwort)
    }

    @Command
    fun speichern(invoke: Invoke) {
        val args = try {
            invoke.parseArgs(FachArgs::class.java)
        } catch (e: Exception) {
            invoke.reject("Unvollständiger Auftrag an das Schlüsselfach")
            return
        }
        val geheimnis = args.geheimnis
        if (geheimnis == null) {
            invoke.reject("Kein Geheimnis übergeben")
            return
        }

        try {
            Schluesselfach.pruefeFach(args.fach)
            if (Schluesselfach.brauchtBestaetigung(args.fach)) {
                starteAbfrage(
                    invoke,
                    args.fach,
                    SchluesselfachActivity.MODUS_ABLEGEN,
                    args.nachricht ?: "Schnelleinstieg einrichten",
                    geheimnis,
                ) { invoke.resolve() }
            } else {
                val cipher = Schluesselfach.cipherZumAblegen(args.fach)
                Schluesselfach.legeAb(activity, args.fach, cipher, geheimnis)
                invoke.resolve()
            }
        } catch (e: Exception) {
            invoke.reject(e.message ?: "Das Geheimnis konnte nicht verwahrt werden")
        }
    }

    @Command
    fun lesen(invoke: Invoke) {
        val args = try {
            invoke.parseArgs(FachArgs::class.java)
        } catch (e: Exception) {
            invoke.reject("Unvollständiger Auftrag an das Schlüsselfach")
            return
        }

        try {
            Schluesselfach.pruefeFach(args.fach)
            if (Schluesselfach.brauchtBestaetigung(args.fach)) {
                starteAbfrage(
                    invoke,
                    args.fach,
                    SchluesselfachActivity.MODUS_LESEN,
                    args.nachricht ?: "Entsperren",
                    null,
                ) { wert -> invoke.resolve(mitWert(wert)) }
            } else {
                val cipher = Schluesselfach.cipherZumLesen(activity, args.fach)
                val wert = cipher?.let { Schluesselfach.lies(activity, args.fach, it) }
                invoke.resolve(mitWert(wert))
            }
        } catch (e: Exception) {
            invoke.reject(e.message ?: "Das Geheimnis konnte nicht gelesen werden")
        }
    }

    @Command
    fun loeschen(invoke: Invoke) {
        val args = try {
            invoke.parseArgs(FachArgs::class.java)
        } catch (e: Exception) {
            invoke.reject("Unvollständiger Auftrag an das Schlüsselfach")
            return
        }
        try {
            // Löschen fragt nicht nach. Ein Fach zurückzunehmen ist die sichere
            // Richtung: wer abschaltet, verliert eine Abkürzung, nichts sonst.
            // Eine Abfrage davor hieße, dass ein ungültig gewordener Schlüssel
            // sich nicht mehr wegräumen ließe.
            Schluesselfach.loesche(activity, args.fach)
            invoke.resolve()
        } catch (e: Exception) {
            invoke.reject(e.message ?: "Das Fach konnte nicht geleert werden")
        }
    }

    private fun mitWert(wert: String?): JSObject {
        val antwort = JSObject()
        // `JSObject.put(key, null)` nimmt den Schlüssel heraus statt ihn auf
        // null zu setzen; drüben steht dafür ein `Option` mit Vorgabe.
        if (wert != null) antwort.put("wert", wert)
        return antwort
    }

    /**
     * Schickt den Auftrag durch die Abfrage und beantwortet den Aufruf, sobald
     * sie durch ist.
     */
    private fun starteAbfrage(
        invoke: Invoke,
        fach: String,
        modus: String,
        nachricht: String,
        geheimnis: String?,
        beiErfolg: (String?) -> Unit,
    ) {
        val gestartet = SchluesselfachActivity.starte(activity, fach, modus, nachricht, geheimnis) { ergebnis ->
            ergebnis.fold(
                onSuccess = { beiErfolg(it) },
                onFailure = { invoke.reject(it.message ?: "Bestätigung fehlgeschlagen") },
            )
        }
        if (!gestartet) {
            invoke.reject("Es läuft bereits eine Abfrage.")
        }
    }
}
