package com.mauntingstudios.smart_system

import android.Manifest
import android.app.Activity
import android.content.ContentUris
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import android.util.Base64
import androidx.activity.result.ActivityResult
import androidx.activity.result.IntentSenderRequest
import androidx.core.content.ContextCompat
import app.tauri.annotation.ActivityCallback
import app.tauri.annotation.Command
import app.tauri.annotation.InvokeArg
import app.tauri.annotation.Permission
import app.tauri.annotation.PermissionCallback
import app.tauri.annotation.TauriPlugin
import app.tauri.plugin.Invoke
import app.tauri.plugin.JSArray
import app.tauri.plugin.JSObject
import app.tauri.plugin.Plugin
import java.io.FileInputStream
import java.security.MessageDigest
import java.util.concurrent.Executors

@InvokeArg
class AufnahmenArgs {
    var nachId: Long = 0
    var hoechstens: Int = 100
}

@InvokeArg
class MedienArgs {
    var id: Long = 0
    lateinit var art: String
    var von: Long = 0
    var laenge: Int = 0
}

@InvokeArg
class PapierkorbArgs {
    var bilder: LongArray = LongArray(0)
    var videos: LongArray = LongArray(0)
}

/**
 * Lesender Zugriff auf die Kameraaufnahmen dieses Geräts, für die Kamera-Sicherung
 * des Tresors.
 *
 * Nur Kennung und Art kommen von drüben, die Adresse baut dieses Plugin selbst.
 * Eine Adresse von außen wäre ein Weg, beliebige Content-Provider zu lesen,
 * auch den eigenen FileProvider.
 *
 * Gelesen wird das Original mit Aufnahmeort (`setRequireOriginal`). Ohne das
 * entfernt Android den Ort aus den Bytes, und nach „Speicher freigeben“ wäre er
 * für immer weg. Ohne `ACCESS_MEDIA_LOCATION` gilt der Zugriff deshalb als
 * unvollständig.
 */
@TauriPlugin(
    permissions = [
        Permission(strings = [Manifest.permission.READ_MEDIA_IMAGES, Manifest.permission.READ_MEDIA_VIDEO], alias = "medien"),
        Permission(strings = [Manifest.permission.READ_EXTERNAL_STORAGE], alias = "speicher"),
        Permission(strings = [Manifest.permission.ACCESS_MEDIA_LOCATION], alias = "ort"),
    ],
)
class MedienPlugin(private val activity: Activity) : Plugin(activity) {

    // Lesen und Prüfsummen laufen nie auf dem Hauptthread.
    private val arbeit = Executors.newSingleThreadExecutor()

    private fun aliase(): Array<String> = when {
        Build.VERSION.SDK_INT >= 33 -> arrayOf("medien", "ort")
        Build.VERSION.SDK_INT >= 29 -> arrayOf("speicher", "ort")
        else -> arrayOf("speicher")
    }

    private fun erlaubt(name: String): Boolean =
        ContextCompat.checkSelfPermission(activity, name) == PackageManager.PERMISSION_GRANTED

    /** "voll", "teilweise" (Android 14: nur ausgewählte Fotos) oder "keiner". */
    private fun stand(): String {
        val lesen = if (Build.VERSION.SDK_INT >= 33) {
            erlaubt(Manifest.permission.READ_MEDIA_IMAGES) && erlaubt(Manifest.permission.READ_MEDIA_VIDEO)
        } else {
            erlaubt(Manifest.permission.READ_EXTERNAL_STORAGE)
        }
        val ort = Build.VERSION.SDK_INT < 29 || erlaubt(Manifest.permission.ACCESS_MEDIA_LOCATION)
        if (lesen && ort) return "voll"
        if (lesen) return "teilweise"
        if (Build.VERSION.SDK_INT >= 34 && erlaubt(Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED)) return "teilweise"
        return "keiner"
    }

    private fun mitStand(): JSObject = JSObject().apply {
        put("stand", stand())
        put("papierkorb", Build.VERSION.SDK_INT >= 30)
    }

    @Command
    fun zugriff(invoke: Invoke) {
        invoke.resolve(mitStand())
    }

    @Command
    fun zugriffAnfragen(invoke: Invoke) {
        if (stand() == "voll") {
            invoke.resolve(mitStand())
            return
        }
        requestPermissionForAliases(aliase(), invoke, "nachAnfrage")
    }

    @PermissionCallback
    private fun nachAnfrage(invoke: Invoke) {
        invoke.resolve(mitStand())
    }

    private fun basis(art: String): Uri = when (art) {
        "bild" -> MediaStore.Images.Media.EXTERNAL_CONTENT_URI
        "video" -> MediaStore.Video.Media.EXTERNAL_CONTENT_URI
        else -> throw IllegalArgumentException("Unbekannte Art")
    }

    private fun adresse(id: Long, art: String): Uri {
        if (id <= 0) throw IllegalArgumentException("Ungültige Kennung")
        val uri = ContentUris.withAppendedId(basis(art), id)
        return if (Build.VERSION.SDK_INT >= 29) MediaStore.setRequireOriginal(uri) else uri
    }

    /** Aufnahmen aus DCIM mit größerer Kennung als `nachId`, aufsteigend. */
    private fun suche(basis: Uri, art: String, nachId: Long, hoechstens: Int): List<JSObject> {
        val spalten = mutableListOf(
            MediaStore.MediaColumns._ID,
            MediaStore.MediaColumns.DISPLAY_NAME,
            MediaStore.MediaColumns.MIME_TYPE,
            MediaStore.MediaColumns.SIZE,
            MediaStore.MediaColumns.DATE_ADDED,
            MediaStore.MediaColumns.DATE_MODIFIED,
        )
        if (Build.VERSION.SDK_INT >= 29) spalten.add(MediaStore.MediaColumns.DATE_TAKEN)
        // Nur die Kamera-Ordner, keine Screenshots und keine Messenger-Bilder.
        val (ort, ortWert) = if (Build.VERSION.SDK_INT >= 29) {
            "${MediaStore.MediaColumns.RELATIVE_PATH} LIKE ?" to "DCIM/%"
        } else {
            @Suppress("DEPRECATION")
            "${MediaStore.MediaColumns.DATA} LIKE ?" to "%/DCIM/%"
        }
        var auswahl = "${MediaStore.MediaColumns._ID} > ? AND $ort"
        if (Build.VERSION.SDK_INT >= 29) auswahl += " AND ${MediaStore.MediaColumns.IS_PENDING} = 0"
        val ergebnis = mutableListOf<JSObject>()
        activity.contentResolver.query(
            basis,
            spalten.toTypedArray(),
            auswahl,
            arrayOf(nachId.toString(), ortWert),
            "${MediaStore.MediaColumns._ID} ASC",
        )?.use { c ->
            val iId = c.getColumnIndexOrThrow(MediaStore.MediaColumns._ID)
            val iName = c.getColumnIndexOrThrow(MediaStore.MediaColumns.DISPLAY_NAME)
            val iTyp = c.getColumnIndexOrThrow(MediaStore.MediaColumns.MIME_TYPE)
            val iGroesse = c.getColumnIndexOrThrow(MediaStore.MediaColumns.SIZE)
            val iHinzu = c.getColumnIndexOrThrow(MediaStore.MediaColumns.DATE_ADDED)
            val iGeaendert = c.getColumnIndexOrThrow(MediaStore.MediaColumns.DATE_MODIFIED)
            val iAufnahme = if (Build.VERSION.SDK_INT >= 29) c.getColumnIndex(MediaStore.MediaColumns.DATE_TAKEN) else -1
            while (c.moveToNext() && ergebnis.size < hoechstens) {
                val aufnahme = if (iAufnahme >= 0 && !c.isNull(iAufnahme)) c.getLong(iAufnahme) else c.getLong(iHinzu) * 1000
                ergebnis.add(JSObject().apply {
                    put("id", c.getLong(iId))
                    put("art", art)
                    put("name", c.getString(iName) ?: "")
                    put("typ", c.getString(iTyp) ?: "")
                    put("groesse", c.getLong(iGroesse))
                    put("aufgenommen", aufnahme)
                    put("geaendert", c.getLong(iGeaendert) * 1000)
                })
            }
        }
        return ergebnis
    }

    @Command
    fun aufnahmen(invoke: Invoke) {
        val args = try {
            invoke.parseArgs(AufnahmenArgs::class.java)
        } catch (e: Exception) {
            invoke.reject("Unvollständige Anfrage")
            return
        }
        if (stand() == "keiner") {
            invoke.reject("Kein Zugriff auf Fotos und Videos", "KEIN_ZUGRIFF")
            return
        }
        val hoechstens = args.hoechstens.coerceIn(1, 500)
        arbeit.execute {
            try {
                // Bilder und Videos teilen sich die Kennungen der Medientabelle.
                val alle = (suche(basis("bild"), "bild", args.nachId, hoechstens) +
                    suche(basis("video"), "video", args.nachId, hoechstens))
                    .sortedBy { it.getLong("id") }
                    .take(hoechstens)
                val liste = JSArray()
                for (eintrag in alle) liste.put(eintrag)
                invoke.resolve(JSObject().apply { put("aufnahmen", liste) })
            } catch (e: Exception) {
                invoke.reject(e.message ?: "Aufnahmen konnten nicht gelesen werden")
            }
        }
    }

    /** Die größte Kennung, die es gerade gibt: ab hier zählt eine Aufnahme als neu. */
    @Command
    fun hoechsteId(invoke: Invoke) {
        arbeit.execute {
            try {
                var hoechste = 0L
                activity.contentResolver.query(
                    MediaStore.Files.getContentUri("external"),
                    arrayOf(MediaStore.MediaColumns._ID),
                    null,
                    null,
                    "${MediaStore.MediaColumns._ID} DESC",
                )?.use { c -> if (c.moveToFirst()) hoechste = c.getLong(0) }
                invoke.resolve(JSObject().apply { put("id", hoechste) })
            } catch (e: Exception) {
                invoke.reject(e.message ?: "Stand der Aufnahmen unbekannt")
            }
        }
    }

    @Command
    fun lesen(invoke: Invoke) {
        val args = try {
            invoke.parseArgs(MedienArgs::class.java)
        } catch (e: Exception) {
            invoke.reject("Unvollständige Anfrage")
            return
        }
        if (args.von < 0 || args.laenge <= 0 || args.laenge > HOECHSTENS_LESEN) {
            invoke.reject("Ungültiger Ausschnitt")
            return
        }
        arbeit.execute {
            try {
                val uri = adresse(args.id, args.art)
                val puffer = ByteArray(args.laenge)
                var gelesen = 0
                activity.contentResolver.openFileDescriptor(uri, "r")?.use { fd ->
                    FileInputStream(fd.fileDescriptor).use { ein ->
                        val kanal = ein.channel
                        kanal.position(args.von)
                        while (gelesen < puffer.size) {
                            val n = ein.read(puffer, gelesen, puffer.size - gelesen)
                            if (n < 0) break
                            gelesen += n
                        }
                    }
                } ?: throw IllegalStateException("Aufnahme nicht lesbar")
                // Bytes reisen als Base64 im JSON (AGENTS.md Punkt 69).
                invoke.resolve(JSObject().apply {
                    put("daten", Base64.encodeToString(puffer, 0, gelesen, Base64.NO_WRAP))
                })
            } catch (e: Exception) {
                invoke.reject(e.message ?: "Aufnahme nicht lesbar")
            }
        }
    }

    @Command
    fun pruefsumme(invoke: Invoke) {
        val args = try {
            invoke.parseArgs(MedienArgs::class.java)
        } catch (e: Exception) {
            invoke.reject("Unvollständige Anfrage")
            return
        }
        arbeit.execute {
            try {
                val sha = MessageDigest.getInstance("SHA-256")
                var groesse = 0L
                activity.contentResolver.openInputStream(adresse(args.id, args.art))?.use { ein ->
                    val puffer = ByteArray(1 shl 20)
                    while (true) {
                        val n = ein.read(puffer)
                        if (n < 0) break
                        sha.update(puffer, 0, n)
                        groesse += n
                    }
                } ?: throw IllegalStateException("Aufnahme nicht lesbar")
                invoke.resolve(JSObject().apply {
                    put("sha256", sha.digest().joinToString("") { "%02x".format(it) })
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
        val args = try {
            invoke.parseArgs(PapierkorbArgs::class.java)
        } catch (e: Exception) {
            invoke.reject("Unvollständige Anfrage")
            return
        }
        try {
            val uris = args.bilder.map { ContentUris.withAppendedId(basis("bild"), it) } +
                args.videos.map { ContentUris.withAppendedId(basis("video"), it) }
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
        /** Ein Ausschnitt je Aufruf, etwas mehr als ein Tresor-Chunk (4 MiB). */
        const val HOECHSTENS_LESEN = 8 * 1024 * 1024
    }
}
