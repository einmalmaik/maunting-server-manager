package com.mauntingstudios.smart_system

import android.Manifest
import android.content.ContentUris
import android.content.Context
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import androidx.core.content.ContextCompat
import java.security.MessageDigest

/** Eine Aufnahme aus DCIM (auf Wunsch auch ein Bildschirmfoto), so wie der MediaStore sie nennt. */
data class Aufnahme(
    val id: Long,
    val marke: Long,
    val art: String,
    val name: String,
    val typ: String,
    val aufgenommen: Long,
    val geaendert: Long,
)

/**
 * Lesender Zugriff auf die Kameraaufnahmen, gemeinsam für das Plugin (offene
 * App) und den Hintergrund-Job (`KameraArbeit`).
 *
 * Adressen baut nur dieses Objekt, aus Kennung und Art. Eine Adresse von außen
 * wäre ein Weg, beliebige Content-Provider zu lesen, auch den eigenen
 * FileProvider.
 */
object Medien {
    private fun erlaubt(ctx: Context, name: String): Boolean =
        ContextCompat.checkSelfPermission(ctx, name) == PackageManager.PERMISSION_GRANTED

    /**
     * "voll", "teilweise" (Android 14: nur ausgewählte Fotos) oder "keiner".
     * Ohne `ACCESS_MEDIA_LOCATION` ist der Zugriff unvollständig: Android
     * entfernt dann den Aufnahmeort aus den Bytes, und nach „Speicher
     * freigeben“ wäre er für immer weg.
     */
    fun stand(ctx: Context): String {
        val lesen = if (Build.VERSION.SDK_INT >= 33) {
            erlaubt(ctx, Manifest.permission.READ_MEDIA_IMAGES) && erlaubt(ctx, Manifest.permission.READ_MEDIA_VIDEO)
        } else {
            erlaubt(ctx, Manifest.permission.READ_EXTERNAL_STORAGE)
        }
        val ort = Build.VERSION.SDK_INT < 29 || erlaubt(ctx, Manifest.permission.ACCESS_MEDIA_LOCATION)
        if (lesen && ort) return "voll"
        if (lesen) return "teilweise"
        if (Build.VERSION.SDK_INT >= 34 && erlaubt(ctx, Manifest.permission.READ_MEDIA_VISUAL_USER_SELECTED)) return "teilweise"
        return "keiner"
    }

    fun basis(art: String): Uri = when (art) {
        "bild" -> MediaStore.Images.Media.EXTERNAL_CONTENT_URI
        "video" -> MediaStore.Video.Media.EXTERNAL_CONTENT_URI
        else -> throw IllegalArgumentException("Unbekannte Art")
    }

    /** Die Adresse des Originals, mit Aufnahmeort. */
    fun original(id: Long, art: String): Uri {
        val uri = adresse(id, art)
        return if (Build.VERSION.SDK_INT >= 29) MediaStore.setRequireOriginal(uri) else uri
    }

    fun adresse(id: Long, art: String): Uri {
        require(id > 0) { "Ungültige Kennung" }
        return ContentUris.withAppendedId(basis(art), id)
    }

    /**
     * Woran eine Aufnahme als neu erkannt wird. Ab Android 11 die Generation der
     * letzten Änderung: eine Aufnahme, die noch geschrieben wird (`IS_PENDING`,
     * etwa ein laufendes Video), bekommt beim Fertigwerden eine neue, höhere
     * Generation und fällt nicht hinter eine spätere zurück. Davor gibt es nur
     * die Kennung (AGENTS.md Punkt 99).
     */
    private val markeSpalte =
        if (Build.VERSION.SDK_INT >= 30) MediaStore.MediaColumns.GENERATION_MODIFIED else MediaStore.MediaColumns._ID

    /**
     * Die Marke, die es gerade gibt (ab hier zählt eine Aufnahme als neu), und
     * die Fassung des MediaStore. Baut Android ihn neu auf, beginnen die
     * Generationen von vorn, und eine alte Marke fände nie wieder etwas.
     */
    fun jetzt(ctx: Context): Pair<Long, String> {
        if (Build.VERSION.SDK_INT >= 30) {
            return MediaStore.getGeneration(ctx, MediaStore.VOLUME_EXTERNAL) to
                "gen:" + MediaStore.getVersion(ctx, MediaStore.VOLUME_EXTERNAL)
        }
        var hoechste = 0L
        ctx.contentResolver.query(
            MediaStore.Files.getContentUri("external"),
            arrayOf(MediaStore.MediaColumns._ID),
            null,
            null,
            "${MediaStore.MediaColumns._ID} DESC",
        )?.use { c -> if (c.moveToFirst()) hoechste = c.getLong(0) }
        return hoechste to "id"
    }

    /**
     * Fertige Aufnahmen aus DCIM hinter der Stelle (`nach`, `nachId`),
     * aufsteigend nach Marke und Kennung; Bilder und Videos gemischt. Die
     * Kennung zählt mit, weil viele Aufnahmen dieselbe Generation tragen können.
     */
    fun aufnahmen(ctx: Context, nach: Long, nachId: Long, hoechstens: Int, screenshotsAb: Long?): List<Aufnahme> =
        (suche(ctx, "bild", nach, hoechstens, screenshotsAb, nachId = nachId) + suche(ctx, "video", nach, hoechstens, screenshotsAb, nachId = nachId))
            .sortedWith(compareBy({ it.marke }, { it.id }))
            .take(hoechstens)

    /** Eine Aufnahme nach Kennung, wenn sie noch fertig in einem gesicherten Ordner liegt. */
    fun aufnahme(ctx: Context, id: Long, art: String, screenshotsAb: Long?): Aufnahme? =
        suche(ctx, art, 0, 1, screenshotsAb, nurId = id).firstOrNull()

    private fun suche(
        ctx: Context,
        art: String,
        nach: Long,
        hoechstens: Int,
        /** Bildschirmfotos ab dieser Marke; `null`: keine. */
        screenshotsAb: Long?,
        nurId: Long? = null,
        nachId: Long = KameraAblage.ALLE,
    ): List<Aufnahme> {
        val spalten = mutableListOf(
            MediaStore.MediaColumns._ID,
            markeSpalte,
            MediaStore.MediaColumns.DISPLAY_NAME,
            MediaStore.MediaColumns.MIME_TYPE,
            MediaStore.MediaColumns.DATE_ADDED,
            MediaStore.MediaColumns.DATE_MODIFIED,
        )
        if (Build.VERSION.SDK_INT >= 29) spalten.add(MediaStore.MediaColumns.DATE_TAKEN)
        // Die Kamera-Ordner; Bildschirmfotos nur, wenn eingeschaltet. Manche Hersteller legen sie
        // unter DCIM ab, deshalb zählen sie dort nicht als Kamera. Messenger-Bilder nie.
        val (pfad, vor) = if (Build.VERSION.SDK_INT >= 29) {
            MediaStore.MediaColumns.RELATIVE_PATH to ""
        } else {
            @Suppress("DEPRECATION")
            MediaStore.MediaColumns.DATA to "%/"
        }
        val kamera = "($pfad LIKE ? AND $pfad NOT LIKE ?)"
        val bildschirm = "(($pfad LIKE ? OR $pfad LIKE ?) AND $markeSpalte > ?)"
        val ort = if (screenshotsAb != null) "($kamera OR $bildschirm)" else kamera
        val id = MediaStore.MediaColumns._ID
        var auswahl = "($markeSpalte > ? OR ($markeSpalte = ? AND $id > ?)) AND $ort"
        if (Build.VERSION.SDK_INT >= 29) auswahl += " AND ${MediaStore.MediaColumns.IS_PENDING} = 0"
        val werte = mutableListOf(nach.toString(), nach.toString(), nachId.toString(), "${vor}DCIM/%", "${vor}DCIM/Screenshots/%")
        if (screenshotsAb != null) werte += listOf("${vor}DCIM/Screenshots/%", "${vor}Pictures/Screenshots/%", screenshotsAb.toString())
        if (nurId != null) {
            auswahl += " AND ${MediaStore.MediaColumns._ID} = ?"
            werte.add(nurId.toString())
        }
        val ergebnis = mutableListOf<Aufnahme>()
        ctx.contentResolver.query(basis(art), spalten.toTypedArray(), auswahl, werte.toTypedArray(), "$markeSpalte ASC, $id ASC")?.use { c ->
            val iId = c.getColumnIndexOrThrow(MediaStore.MediaColumns._ID)
            val iMarke = c.getColumnIndexOrThrow(markeSpalte)
            val iName = c.getColumnIndexOrThrow(MediaStore.MediaColumns.DISPLAY_NAME)
            val iTyp = c.getColumnIndexOrThrow(MediaStore.MediaColumns.MIME_TYPE)
            val iHinzu = c.getColumnIndexOrThrow(MediaStore.MediaColumns.DATE_ADDED)
            val iGeaendert = c.getColumnIndexOrThrow(MediaStore.MediaColumns.DATE_MODIFIED)
            val iAufnahme = if (Build.VERSION.SDK_INT >= 29) c.getColumnIndex(MediaStore.MediaColumns.DATE_TAKEN) else -1
            while (c.moveToNext() && ergebnis.size < hoechstens) {
                val aufnahme = if (iAufnahme >= 0 && !c.isNull(iAufnahme)) c.getLong(iAufnahme) else c.getLong(iHinzu) * 1000
                ergebnis.add(
                    Aufnahme(
                        id = c.getLong(iId),
                        marke = c.getLong(iMarke),
                        art = art,
                        name = c.getString(iName) ?: "",
                        typ = c.getString(iTyp) ?: "",
                        aufgenommen = aufnahme,
                        geaendert = c.getLong(iGeaendert) * 1000,
                    ),
                )
            }
        }
        return ergebnis
    }

    /** SHA-256 (hex) und Größe des Originals. `FileNotFoundException`, wenn die Aufnahme nicht mehr da ist. */
    fun pruefsumme(ctx: Context, id: Long, art: String): Pair<String, Long> {
        val sha = MessageDigest.getInstance("SHA-256")
        var groesse = 0L
        ctx.contentResolver.openInputStream(original(id, art))?.use { ein ->
            val puffer = ByteArray(1 shl 20)
            while (true) {
                val n = ein.read(puffer)
                if (n < 0) break
                sha.update(puffer, 0, n)
                groesse += n
            }
        } ?: throw java.io.IOException("Aufnahme gerade nicht lesbar")
        return hex(sha.digest()) to groesse
    }

    /** Liest `laenge` Bytes ab `von` aus dem Original; kürzer nur, wenn die Datei kürzer geworden ist. */
    fun lesen(ctx: Context, id: Long, art: String, von: Long, laenge: Int): ByteArray {
        val puffer = ByteArray(laenge)
        var gelesen = 0
        ctx.contentResolver.openFileDescriptor(original(id, art), "r")?.use { fd ->
            java.io.FileInputStream(fd.fileDescriptor).use { ein ->
                ein.channel.position(von)
                while (gelesen < laenge) {
                    val n = ein.read(puffer, gelesen, laenge - gelesen)
                    if (n < 0) break
                    gelesen += n
                }
            }
        } ?: throw IllegalStateException("Aufnahme nicht lesbar")
        return if (gelesen == laenge) puffer else puffer.copyOf(gelesen)
    }

    fun hex(bytes: ByteArray): String = bytes.joinToString("") { "%02x".format(it) }
}
