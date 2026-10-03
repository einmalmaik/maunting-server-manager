package com.mauntingstudios.smart_system

import android.Manifest
import android.app.Activity
import android.content.ContentUris
import android.os.Build
import android.provider.MediaStore
import android.util.Base64
import android.webkit.WebView
import androidx.activity.result.ActivityResult
import androidx.activity.result.IntentSenderRequest
import app.tauri.annotation.ActivityCallback
import app.tauri.annotation.Command
import app.tauri.annotation.InvokeArg
import app.tauri.annotation.Permission
import app.tauri.annotation.PermissionCallback
import app.tauri.annotation.TauriPlugin
import app.tauri.plugin.Invoke
import app.tauri.plugin.JSObject
import app.tauri.plugin.Plugin
import java.util.UUID
import java.util.concurrent.Executors

@InvokeArg
class MedienArgs {
    var id: Long = 0
    lateinit var art: String
}

@InvokeArg
class SicherungArgs {
    lateinit var geraet: String
}

@InvokeArg
class EinrichtenArgs {
    var konto: Long = 0
    lateinit var server: String
    lateinit var bucket: String
    lateinit var geraet: String
    lateinit var zugang: String
    lateinit var eingangId: String
    lateinit var pq: String
    lateinit var rsa: String
    var nurWlan: Boolean = false
}

@InvokeArg
class AendernArgs {
    var nurWlan: Boolean? = null
    var screenshots: Boolean? = null
    /** Auch sichern, was vor dem Einschalten aufgenommen wurde. */
    var vorhandene: Boolean = false
    /** Was schon im Tresor liegt (`medienId:sha256`), damit es nicht noch einmal hochgeht. */
    var bekannt: Array<String> = emptyArray()
}

@InvokeArg
class PapierkorbArgs {
    var bilder: LongArray = LongArray(0)
    var videos: LongArray = LongArray(0)
}

/**
 * Die Kamera-Sicherung des Tresors von der App aus: Zugriff erfragen,
 * einrichten, Stand zeigen, Speicher freigeben. Gesichert wird nur im
 * Hintergrund-Job (`KameraArbeit`), auch bei offener App.
 *
 * Gelesen wird das Original mit Aufnahmeort (`Medien.original`). Ohne
 * `ACCESS_MEDIA_LOCATION` gilt der Zugriff deshalb als unvollständig.
 */
@TauriPlugin(
    permissions = [
        Permission(strings = [Manifest.permission.READ_MEDIA_IMAGES, Manifest.permission.READ_MEDIA_VIDEO], alias = "medien"),
        Permission(strings = [Manifest.permission.READ_EXTERNAL_STORAGE], alias = "speicher"),
        Permission(strings = [Manifest.permission.ACCESS_MEDIA_LOCATION], alias = "ort"),
    ],
)
class MedienPlugin(private val activity: Activity) : Plugin(activity) {

    // Lesen, Prüfsummen, Keystore und Ablage laufen nie auf dem Hauptthread.
    private val arbeit = Executors.newSingleThreadExecutor()

    override fun load(webView: WebView) {
        super.load(webView)
        // Nach einem Update oder wenn das System Aufträge verworfen hat: Auslöser wieder aufstellen.
        arbeit.execute { runCatching { KameraPlan.planen(activity.applicationContext) } }
    }

    private fun aliase(): Array<String> = when {
        Build.VERSION.SDK_INT >= 33 -> arrayOf("medien", "ort")
        Build.VERSION.SDK_INT >= 29 -> arrayOf("speicher", "ort")
        else -> arrayOf("speicher")
    }

    private fun mitStand(): JSObject = JSObject().apply {
        put("stand", Medien.stand(activity))
        put("papierkorb", Build.VERSION.SDK_INT >= 30)
    }

    private fun im(invoke: Invoke, fehler: String, aufgabe: () -> JSObject) {
        arbeit.execute {
            try {
                invoke.resolve(aufgabe())
            } catch (e: Exception) {
                invoke.reject(e.message ?: fehler)
            }
        }
    }

    private inline fun <reified T> argumente(invoke: Invoke): T? = try {
        invoke.parseArgs(T::class.java)
    } catch (e: Exception) {
        invoke.reject("Unvollständige Anfrage")
        null
    }

    @Command
    fun zugriff(invoke: Invoke) {
        invoke.resolve(mitStand())
    }

    @Command
    fun zugriffAnfragen(invoke: Invoke) {
        if (Medien.stand(activity) == "voll") {
            invoke.resolve(mitStand())
            return
        }
        requestPermissionForAliases(aliase(), invoke, "nachAnfrage")
    }

    @PermissionCallback
    private fun nachAnfrage(invoke: Invoke) {
        invoke.resolve(mitStand())
    }

    /** Öffentlicher Unterschriftsschlüssel dieses Geräts (`SicherungsSchluessel`), legt ihn bei Bedarf an. */
    @Command
    fun sicherungSchluessel(invoke: Invoke) {
        val args = argumente<SicherungArgs>(invoke) ?: return
        im(invoke, "Schlüssel nicht verfügbar") {
            JSObject().apply { put("spki", Base64.encodeToString(SicherungsSchluessel.oeffentlich(args.geraet), Base64.NO_WRAP)) }
        }
    }

    /**
     * Schaltet die Sicherung für einen Tresor ein oder erneuert den Zugang.
     * Für denselben Tresor, dasselbe Gerät und denselben MediaStore geht es
     * an der bisherigen Marke weiter; sonst zählt erst, was ab jetzt
     * aufgenommen wird.
     */
    @Command
    fun einrichten(invoke: Invoke) {
        val a = argumente<EinrichtenArgs>(invoke) ?: return
        im(invoke, "Kamera-Sicherung nicht eingerichtet") {
            val (marke, fassung) = Medien.jetzt(activity)
            val alt = KameraAblage.lesen(activity)
            val weiter = alt != null && alt.konto == a.konto && alt.bucket == a.bucket && alt.geraet == a.geraet && alt.server == a.server
            if (!weiter) {
                KameraPlan.abbestellen(activity)
                KameraAblage.vergessen(activity)
            }
            KameraAblage.setzen(
                activity,
                KameraAblage.Stand(
                    kennung = if (weiter) alt!!.kennung else UUID.randomUUID().toString(),
                    konto = a.konto,
                    server = a.server,
                    bucket = a.bucket,
                    geraet = a.geraet,
                    zugang = KameraAblage.zu(a.zugang),
                    eingangId = a.eingangId,
                    pq = a.pq,
                    rsa = a.rsa,
                    nurWlan = a.nurWlan,
                    screenshots = if (weiter) alt!!.screenshots else false,
                    screenshotsAb = if (weiter && alt!!.fassung == fassung) alt.screenshotsAb else marke,
                    marke = if (weiter && alt!!.fassung == fassung) alt.marke else marke,
                    markeId = if (weiter && alt!!.fassung == fassung) alt.markeId else KameraAblage.ALLE,
                    fassung = fassung,
                    gesichert = if (weiter) alt!!.gesichert else 0,
                    zuletzt = if (weiter) alt!!.zuletzt else 0,
                ),
            )
            KameraPlan.planen(activity)
            standJson()
        }
    }

    private fun standJson(): JSObject {
        val s = KameraAblage.lesen(activity) ?: return JSObject().apply { put("eingerichtet", false) }
        return JSObject().apply {
            put("eingerichtet", true)
            put("konto", s.konto)
            put("bucket", s.bucket)
            put("geraet", s.geraet)
            put("nurWlan", s.nurWlan)
            put("screenshots", s.screenshots)
            put("gesichert", s.gesichert)
            put("zuletzt", s.zuletzt)
            put("offen", KameraAblage.anzahlAuftraege(activity))
            if (s.warten != null) put("warten", s.warten)
        }
    }

    @Command
    fun stand(invoke: Invoke) {
        im(invoke, "Stand unbekannt") { standJson() }
    }

    @Command
    fun aendern(invoke: Invoke) {
        val a = argumente<AendernArgs>(invoke) ?: return
        im(invoke, "Nicht geändert") {
            val alt = KameraAblage.lesen(activity) ?: throw IllegalStateException("Die Kamera-Sicherung ist aus")
            KameraAblage.bekanntDazu(activity, alt.kennung, a.bekannt.filter { BEKANNT.matches(it) })
            KameraAblage.aendern(activity, alt.kennung) { s ->
                val einschalten = a.screenshots == true && !s.screenshots
                val neu = s.copy(
                    nurWlan = a.nurWlan ?: s.nurWlan,
                    screenshots = a.screenshots ?: s.screenshots,
                    screenshotsAb = if (einschalten) Medien.jetzt(activity).first else s.screenshotsAb,
                )
                if (a.vorhandene) neu.copy(marke = 0, markeId = KameraAblage.ALLE, screenshotsAb = 0) else neu
            }
            KameraPlan.anstossen(activity, ersetzen = a.nurWlan != null && a.nurWlan != alt.nurWlan)
            standJson()
        }
    }

    /** Abmelden, Kontowechsel, Ausschalten. */
    @Command
    fun vergessen(invoke: Invoke) {
        im(invoke, "Nicht vergessen") {
            KameraPlan.abbestellen(activity)
            KameraAblage.vergessen(activity)
            standJson()
        }
    }

    /** Die App ist wieder vorn: gleich nachsehen statt auf den Auslöser zu warten. */
    @Command
    fun jetzt(invoke: Invoke) {
        im(invoke, "Nicht angestoßen") {
            KameraPlan.anstossen(activity)
            standJson()
        }
    }

    @Command
    fun pruefsumme(invoke: Invoke) {
        val args = argumente<MedienArgs>(invoke) ?: return
        arbeit.execute {
            try {
                val (sha, groesse) = Medien.pruefsumme(activity, args.id, args.art)
                invoke.resolve(JSObject().apply {
                    put("sha256", sha)
                    put("groesse", groesse)
                })
            } catch (e: Exception) {
                invoke.reject(e.message ?: "Aufnahme nicht lesbar", "NICHT_LESBAR")
            }
        }
    }

    /**
     * Legt gesicherte Aufnahmen in den Papierkorb der Galerie. Android fragt
     * selbst nach, und die Aufnahmen liegen dort noch 30 Tage.
     */
    @Command
    fun inPapierkorb(invoke: Invoke) {
        if (Build.VERSION.SDK_INT < 30) {
            invoke.reject("Erst ab Android 11", "ZU_ALT")
            return
        }
        val args = argumente<PapierkorbArgs>(invoke) ?: return
        try {
            val uris = args.bilder.map { ContentUris.withAppendedId(Medien.basis("bild"), it) } +
                args.videos.map { ContentUris.withAppendedId(Medien.basis("video"), it) }
            if (uris.isEmpty() || uris.any { ContentUris.parseId(it) <= 0 }) {
                invoke.reject("Keine Aufnahmen gewählt")
                return
            }
            val anfrage = MediaStore.createTrashRequest(activity.contentResolver, uris, true)
            startIntentSenderForResult(invoke, IntentSenderRequest.Builder(anfrage.intentSender).build(), "nachPapierkorb")
        } catch (e: Exception) {
            invoke.reject(e.message ?: "Papierkorb nicht erreichbar")
        }
    }

    @ActivityCallback
    private fun nachPapierkorb(invoke: Invoke, ergebnis: ActivityResult) {
        invoke.resolve(JSObject().apply { put("erledigt", ergebnis.resultCode == Activity.RESULT_OK) })
    }

    companion object {
        private val BEKANNT = Regex("^[1-9][0-9]{0,18}:[0-9a-f]{64}$")
    }
}
